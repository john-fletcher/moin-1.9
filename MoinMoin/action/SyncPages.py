# -*- coding: iso-8859-1 -*-
"""
    MoinMoin - SyncPages action

    This action allows you to synchronise pages of two wikis.

    @copyright: 2006 MoinMoin:AlexanderSchremmer
    @license: GNU GPL, see COPYING for details.
"""

import os
import re
import xmlrpclib

# Compatiblity to Python 2.3
try:
    set
except NameError:
    from sets import Set as set


from MoinMoin import wikiutil, config, user
from MoinMoin.packages import unpackLine, packLine
from MoinMoin.PageEditor import PageEditor, conflict_markers
from MoinMoin.Page import Page
from MoinMoin.wikidicts import Dict, Group
from MoinMoin.wikisync import TagStore, UnsupportedWikiException, SyncPage
from MoinMoin.wikisync import MoinLocalWiki, MoinRemoteWiki, UP, DOWN, BOTH, MIMETYPE_MOIN
from MoinMoin.util.bdiff import decompress, patch, compress, textdiff
from MoinMoin.util import diff3


debug = True


# map sync directions
directions_map = {"up": UP, "down": DOWN, "both": BOTH}


class ActionStatus(Exception): pass


class ActionClass(object):
    INFO, WARN, ERROR = zip(range(3), ("", "<!>", "/!\\")) # used for logging

    def __init__(self, pagename, request):
        self.request = request
        self.pagename = pagename
        self.page = PageEditor(request, pagename)
        self.status = []
        request.flush()

    def log_status(self, level, message="", substitutions=(), raw_suffix=""):
        """ Appends the message with a given importance level to the internal log. """
        self.status.append((level, message, substitutions, raw_suffix))

    def generate_log_table(self):
        """ Transforms self.status into a user readable table. """
        table_line = u"|| %(smiley)s || %(message)s%(raw_suffix)s ||"
        table = []

        for line in self.status:
            macro_args = [line[1]] + list(line[2])
            table.append(table_line % {"smiley": line[0][1], "message":
                macro_args and u"[[GetText2(|%s)]]" % (packLine(macro_args), ),
                "raw_suffix": line[3]})

        return "\n".join(table)

    def parse_page(self):
        """ Parses the parameter page and returns the read arguments. """
        options = {
            "remotePrefix": "",
            "localPrefix": "",
            "remoteWiki": "",
            "pageMatch": None,
            "pageList": None,
            "groupList": None,
            "direction": "foo", # is defaulted below
        }

        options.update(Dict(self.request, self.pagename).get_dict())

        # Convert page and group list strings to lists
        if options["pageList"] is not None:
            options["pageList"] = unpackLine(options["pageList"], ",")
        if options["groupList"] is not None:
            options["groupList"] = unpackLine(options["groupList"], ",")

        options["direction"] = directions_map.get(options["direction"].lower(), BOTH)

        return options

    def fix_params(self, params):
        """ Does some fixup on the parameters. """

        # merge the pageList case into the pageMatch case
        if params["pageList"] is not None:
            params["pageMatch"] = u'|'.join([r'^%s$' % re.escape(name)
                                             for name in params["pageList"]])

        if params["pageMatch"] is not None:
            params["pageMatch"] = re.compile(params["pageMatch"], re.U)

        # we do not support matching or listing pages if there is a group of pages
        if params["groupList"]:
            params["pageMatch"] = None
            params["pageList"] = None

        return params

    def render(self):
        """ Render action

        This action returns a status message.
        """
        _ = self.request.getText

        params = self.fix_params(self.parse_page())

        # XXX aquire readlock on self.page
        try:
            if params["direction"] == UP:
                raise ActionStatus(_("The only supported directions are BOTH and DOWN."))

            if not self.request.cfg.interwikiname:
                raise ActionStatus(_("Please set an interwikiname in your wikiconfig (see HelpOnConfiguration) to be able to use this action."))

            if not params["remoteWiki"]:
                raise ActionStatus(_("Incorrect parameters. Please supply at least the ''remoteWiki'' parameter."))

            local = MoinLocalWiki(self.request, params["localPrefix"], params["pageList"])
            try:
                remote = MoinRemoteWiki(self.request, params["remoteWiki"], params["remotePrefix"], params["pageList"], verbose=debug)
            except UnsupportedWikiException, (msg, ):
                raise ActionStatus(msg)

            if not remote.valid:
                raise ActionStatus(_("The ''remoteWiki'' is unknown."))

            self.sync(params, local, remote)
        except ActionStatus, e:
            msg = u'<p class="error">%s</p>\n' % (e.args[0], )
        else:
            msg = u"%s" % (_("Syncronisation finished."), )

        self.page.saveText(self.page.get_raw_body() + "\n\n" + self.generate_log_table(), 0)
        # XXX release readlock on self.page

        return self.page.send_page(self.request, msg=msg)
    
    def sync(self, params, local, remote):
        """ This method does the syncronisation work.
            Currently, it handles the case where the pages exist on both sides.
            One of the major missing parts is rename handling.
            Now there are a few other cases left that have to be implemented:
                Wiki A    | Wiki B   | Remark
                ----------+----------+------------------------------
                exists    | non-     | Now the wiki knows that the page was renamed.
                with tags | existant | There should be an RPC method that asks
                          |          | for the new name (which could be recorded
                          |          | on page rename). Then the page is
                          |          | renamed in Wiki A as well and the sync
                          |          | is done normally.
                          |          | Every wiki retains a dict that maps
                          |          | (IWID, oldname) => newname and that is
                          |          | updated on every rename. oldname refers
                          |          | to the pagename known by the old wiki (can be
                          |          | gathered from tags).
                ----------+----------+-------------------------------
                exists    | any case | Try a rename search first, then
                          |          | do a sync without considering tags
                with tags | with non | to ensure data integrity.
                          | matching | Hmm, how do we detect this
                          | tags     | case if the unmatching tags are only
                          |          | on the remote side?
                ----------+----------+-------------------------------
        """
        _ = lambda x: x # we will translate it later

        direction = params["direction"]
        if direction == BOTH:
            match_direction = direction
        else:
            match_direction = None

        local_full_iwid = packLine([local.get_iwid(), local.get_interwiki_name()])
        remote_full_iwid = packLine([remote.get_iwid(), remote.get_interwiki_name()])

        self.log_status(self.INFO, _("Syncronisation started -"), raw_suffix=" [[DateTime(%s)]]" % self.page._get_local_timestamp())

        l_pages = local.get_pages()
        r_pages = remote.get_pages(exclude_non_writable=direction != DOWN)

        if params["groupList"]:
            pages_from_groupList = set(local.getGroupItems(params["groupList"]))
            r_pages = SyncPage.filter(r_pages, pages_from_groupList.__contains__)
            l_pages = SyncPage.filter(l_pages, pages_from_groupList.__contains__)

        m_pages = [elem.add_missing_pagename(local, remote) for elem in SyncPage.merge(l_pages, r_pages)]

        self.log_status(self.INFO, _("Got a list of %s local and %s remote pages. This results in %s different pages over-all."),
                        (str(len(l_pages)), str(len(r_pages)), str(len(m_pages))))

        if params["pageMatch"]:
            m_pages = SyncPage.filter(m_pages, params["pageMatch"].match)
            self.log_status(self.INFO, _("After filtering: %s pages"), (str(len(m_pages)), ))

        def handle_page(rp):
            # XXX add locking, acquire read-lock on rp
            if debug:
                self.log_status(ActionClass.INFO, raw_suffix="Processing %r" % rp)

            local_pagename = rp.local_name
            current_page = PageEditor(self.request, local_pagename) # YYY direct access
            comment = u"Local Merge - %r" % (remote.get_interwiki_name() or remote.get_iwid())

            tags = TagStore(current_page)

            matching_tags = tags.fetch(iwid_full=remote.iwid_full, direction=match_direction)
            matching_tags.sort()
            if debug:
                self.log_status(ActionClass.INFO, raw_suffix="Tags: %r [[BR]] All: %r" % (matching_tags, tags.tags))

            # some default values for non matching tags
            normalised_name = None
            remote_rev = None
            local_rev = rp.local_rev # merge against the newest version
            old_contents = ""

            if matching_tags:
                newest_tag = matching_tags[-1]

                local_change = newest_tag.current_rev != rp.local_rev
                remote_change = newest_tag.remote_rev != rp.remote_rev

                # handle some cases where we cannot continue for this page
                if not remote_change and (direction == DOWN or not local_change):
                    return # no changes done, next page
                if rp.local_deleted and rp.remote_deleted:
                    return
                if rp.remote_deleted and not local_change:
                    msg = local.delete_page(rp.local_name, comment)
                    if not msg:
                        self.log_status(ActionClass.INFO, _("Deleted page %s locally."), (rp.name, ))
                    else:
                        self.log_status(ActionClass.ERROR, _("Error while deleting page %s locally:"), (rp.name, ), msg)
                    return
                if rp.local_deleted and not remote_change:
                    if direction == DOWN:
                        return
                    self.log_status(ActionClass.ERROR, "Nothing done, I should have deleted %r remotely" % rp) # XXX add
                    msg = remote.delete_page(rp.remote_name)
                    self.log_status(ActionClass.INFO, _("Deleted page %s remotely."), (rp.name, ))
                    return
                if rp.local_mime_type != MIMETYPE_MOIN and not (local_change ^ remote_change):
                    self.log_status(ActionClass.WARN, _("The item %s cannot be merged but was changed in both wikis. Please delete it in one of both wikis and try again."), (rp.name, ))
                    return
                if rp.local_mime_type != rp.remote_mime_type:
                    self.log_status(ActionClass.WARN, _("The item %s has different mime types in both wikis and cannot be merged. Please delete it in one of both wikis or unify the mime type, and try again."), (rp.name, ))
                    return
                if newest_tag.normalised_name != rp.name:
                    self.log_status(ActionClass.WARN, _("The item %s was renamed locally. This is not implemented yet. Therefore the full syncronisation history is lost for this page."), (rp.name, )) # XXX implement renames
                else:
                    normalised_name = newest_tag.normalised_name
                    local_rev = newest_tag.current_rev
                    remote_rev = newest_tag.remote_rev
                    old_contents = Page(self.request, local_pagename, rev=newest_tag.current_rev).get_raw_body_str() # YYY direct access

            self.log_status(ActionClass.INFO, _("Synchronising page %s with remote page %s ..."), (local_pagename, rp.remote_name))

            if direction == DOWN:
                remote_rev = None # always fetch the full page, ignore remote conflict check
                patch_base_contents = ""
            else:
                patch_base_contents = old_contents

            if remote_rev != rp.remote_rev:
                if rp.remote_deleted: # ignore remote changes
                    current_remote_rev = rp.remote_rev
                    is_remote_conflict = False
                    diff = None
                    self.log_status(ActionClass.WARN, _("The page %s was deleted remotely but changed locally."), (rp.name, ))
                else:
                    diff_result = remote.get_diff(rp.remote_name, remote_rev, None, normalised_name)
                    if diff_result is None:
                        self.log_status(ActionClass.ERROR, _("The page %s could not be synced. The remote page was renamed. This is not supported yet. You may want to delete one of the pages to get it synced."), (rp.remote_name, ))
                        return
                    is_remote_conflict = diff_result["conflict"]
                    assert diff_result["diffversion"] == 1
                    diff = diff_result["diff"]
                    current_remote_rev = diff_result["current"]
            else:
                current_remote_rev = remote_rev
                if rp.local_mime_type == MIMETYPE_MOIN:
                    is_remote_conflict = wikiutil.containsConflictMarker(old_contents.decode("utf-8"))
                else:
                    is_remote_conflict = NotImplemented
                diff = None

            # do not sync if the conflict is remote and local, or if it is local
            # and the page has never been syncronised
            if (rp.local_mime_type == MIMETYPE_MOIN and wikiutil.containsConflictMarker(current_page.get_raw_body())
                and (remote_rev is None or is_remote_conflict)):
                self.log_status(ActionClass.WARN, _("Skipped page %s because of a locally or remotely unresolved conflict."), (local_pagename, ))
                return

            if remote_rev is None and direction == BOTH:
                self.log_status(ActionClass.INFO, _("This is the first synchronisation between this page and the remote wiki."))

            if rp.remote_deleted:
                new_contents = ""
            elif diff is None:
                new_contents = old_contents
            else:
                new_contents = patch(patch_base_contents, decompress(diff))

            if rp.local_mime_type == MIMETYPE_MOIN:
                new_contents_unicode = new_contents.decode("utf-8")
                # here, the actual 3-way merge happens
                if debug:
                    self.log_status(ActionClass.INFO, raw_suffix="Merging %r, %r and %r" % (old_contents.decode("utf-8"), new_contents_unicode, current_page.get_raw_body()))
                verynewtext = diff3.text_merge(old_contents.decode("utf-8"), new_contents_unicode, current_page.get_raw_body(), 2, *conflict_markers)
                verynewtext_raw = verynewtext.encode("utf-8")
            else:
                if diff is None:
                    verynewtext_raw = new_contents
                else:
                    verynewtext_raw = current_page.get_raw_body_str()

            diff = textdiff(new_contents, verynewtext_raw)
            if debug:
                self.log_status(ActionClass.INFO, raw_suffix="Diff against %r" % new_contents)

            # XXX upgrade to write lock
            try:
                current_page.saveText(verynewtext, rp.local_rev, comment=comment) # YYY direct access
            except PageEditor.Unchanged:
                pass
            except PageEditor.EditConflict:
                assert False, "You stumbled on a problem with the current storage system - I cannot lock pages"

            new_local_rev = current_page.get_real_rev()

            if direction == BOTH:
                try:
                    very_current_remote_rev = remote.merge_diff(rp.remote_name, compress(diff), new_local_rev, current_remote_rev, current_remote_rev, local_full_iwid, rp.name)
                except Exception, e:
                    raise # XXX rollback locally and do not tag locally
            else:
                very_current_remote_rev = current_remote_rev

            tags.add(remote_wiki=remote_full_iwid, remote_rev=very_current_remote_rev, current_rev=new_local_rev, direction=direction, normalised_name=rp.name)

            if rp.local_mime_type != MIMETYPE_MOIN or not wikiutil.containsConflictMarker(verynewtext):
                self.log_status(ActionClass.INFO, _("Page successfully merged."))
            else:
                self.log_status(ActionClass.WARN, _("Page merged with conflicts."))

            # XXX release lock

        for rp in m_pages:
            handle_page(rp)


def execute(pagename, request):
    ActionClass(pagename, request).render()
