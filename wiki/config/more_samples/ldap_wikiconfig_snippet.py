    # This is a sample configuration snippet that shows how to use the ldap auth plugin.
    # See HelpOnAuthentication and HelpOnConfiguration for more infos.

    from MoinMoin.auth.ldap_login import LDAPAuth
    ldap_authenticator1 = LDAPAuth(
        # the values shown below are the DEFAULT values (you may remove them if you are happy with them),
        # the examples shown in the comments are typical for Active Directory (AD) or OpenLDAP.
        server_uri='ldap://localhost',  # ldap / active directory server URI
                                        # use ldaps://server:636 url for ldaps,
                                        # use  ldap://server for ldap without tls (and set start_tls to 0),
                                        # use  ldap://server for ldap with tls (and set start_tls to 1 or 2).
        bind_dn='',  # We can either use some fixed user and password for binding to LDAP.
                     # Be careful if you need a % char in those strings - as they are used as
                     # a format string, you have to write %% to get a single % in the end.
                     #bind_dn = 'binduser@example.org' # (AD)
                     #bind_dn = 'cn=admin,dc=example,dc=org' # (OpenLDAP)
                     #bind_pw = 'secret'
                     # or we can use the username and password we got from the user:
                     #bind_dn = '%(username)s@example.org' # DN we use for first bind (AD)
                     #bind_pw = '%(password)s' # password we use for first bind
                     # or we can bind anonymously (if that is supported by your directory).
                     # In any case, bind_dn and bind_pw must be defined.
        bind_pw='',
        base_dn='',  # base DN we use for searching
                     #base_dn = 'ou=SOMEUNIT,dc=example,dc=org'
        scope=2, # scope of the search we do (2 == ldap.SCOPE_SUBTREE)
        referrals=0, # LDAP REFERRALS (0 needed for AD)
        search_filter='(uid=%(username)s)',  # ldap filter used for searching:
                                             #search_filter = '(sAMAccountName=%(username)s)' # (AD)
                                             #search_filter = '(uid=%(username)s)' # (OpenLDAP)
                                             # you can also do more complex filtering like:
                                             # "(&(cn=%(username)s)(memberOf=CN=WikiUsers,OU=Groups,DC=example,DC=org))"
        # some attribute names we use to extract information from LDAP (if not None,
        # if None, the attribute won't be extracted from LDAP):
        givenname_attribute=None, # often 'givenName' - ldap attribute we get the first name from
        surname_attribute=None, # often 'sn' - ldap attribute we get the family name from
        aliasname_attribute=None, # often 'displayName' - ldap attribute we get the aliasname from
        email_attribute=None, # often 'mail' - ldap attribute we get the email address from
        email_callback=None, # callback function called to make up email address
        coding='utf-8', # coding used for ldap queries and result values
        timeout=10, # how long we wait for the ldap server [s]
        start_tls=0, # usage of Transport Layer Security 0 = No, 1 = Try, 2 = Required
        tls_cacertdir='',
        tls_cacertfile='',
        tls_certfile='',
        tls_keyfile='',
        tls_require_cert=0, # 0 == ldap.OPT_X_TLS_NEVER (needed for self-signed certs)
        bind_once=False, # set to True to only do one bind - useful if configured to bind as the user on the first attempt
    )

    auth = [ldap_authenticator1, ] # this is a list, you may have multiple ldap authenticators
                                   # as well as other authenticators

    cookie_lifetime = 1 # 1 hour after last access ldap login is required again
    user_autocreate = True

    # customize user preferences (optional, see MoinMoin/config/multiconfig for internal defaults)
    # you maybe want to use user_checkbox_remove, user_checkbox_defaults, user_form_defaults,
    # user_form_disable, user_form_remove.

