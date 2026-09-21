#!/usr/bin/env python3
"""
Sign-in for the study: a NetID and nothing else.

There are no passwords, no registration and no password reset. An annotator
types their NetID, Potato creates the account on the spot if it has not seen it
before, and they are on argument 1. Coming back, they type the same NetID and
land on the argument they left. Somebody who forgets which one they used is
looked up in `annotation_output/`.

WHAT THAT MEANS, once, so it is not discovered later: **the NetID is the whole
credential.** Anyone who has the study link and knows somebody's NetID can
continue as them. That is a deliberate trade for a small internal study where
the roster is known and the alternative -- passwords, a reset flow and a
mailbox to run it -- costs more than it protects. It is `require_password:
false` in config.yaml, and it is one line to reverse.

WHAT THIS FILE STILL DOES, because passwordless does not mean unchecked:

1. **A NetID is validated before it becomes an account.** It is used verbatim
   as a directory name (`user_state_management.py:1045`), so without a rule
   about its shape, `../../something` is a directory anybody can pick. Potato
   applies no such rule.

2. **A NetID is lower-cased.** "ABC123456" and "abc123456" would otherwise be
   two annotators, two directories and two halves of one person's work -- and
   in a study with no password there is nothing else to notice the difference.
   It is also what the sign-in page asks for.

3. **`/register` and `/forgot-password` are turned off.** Neither is linked
   from the passwordless page, but both are still routed, and Potato's
   `/forgot-password` mints a real reset token for any username typed into it
   and prints the link on the page (`routes.py:9485`). With no passwords in the
   study that endpoint has nothing to do and everything to give away, so it
   redirects to the front door instead.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Long enough for an email address, since that is what a non-UTD annotator
# uses. `@` and `+` are allowed for the same reason. What is NOT allowed is the
# point: no slash, no backslash, no "..", nothing that leaves the output
# directory, and no leading dot to hide one.
NETID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._+@-]{1,62})[a-z0-9]$")

NETID_ERROR = ("That does not look like a NetID or an email address. Use 3 to 64 "
               "characters: letters, digits, and . _ + - @")


def clean_netid(raw: str) -> str:
    """What the annotator typed, as it will be stored. '' if unusable."""
    netid = (raw or "").strip().lower()
    if ".." in netid or "/" in netid or "\\" in netid:
        return ""
    return netid if NETID_RE.match(netid) else ""


# --- the sign-in page ---------------------------------------------------------
#
# The hint under the NetID field. Potato has no ui_language key for a second
# line of instructions, and this one carries the only thing an annotator has to
# be told, so it is patched into the template rather than left unsaid.

HINT_ANCHOR = """                    <div class="potato-form-group">
                        <label for="login-email" class="potato-form-label">{{ ui_lang.username_label|default('Username') }}</label>"""

HINT_BLOCK = """                    <p style="color:#6c757d; font-size:0.85rem; line-height:1.45; margin:0 0 1rem;">
                        For UTD students, use your lowercase NetID.
                        For non-UTD students, use your email address.
                    </p>
                    <div class="potato-form-group">
                        <label for="login-email" class="potato-form-label">{{ ui_lang.username_label|default('Username') }}</label>"""


def patch_template(name: str, source: str) -> str:
    """Rewrite Potato's own template as Jinja loads it.

    A single insertion rather than a copy of the file: everything else in
    home.html stays Potato's and keeps following it. A missing anchor is
    reported and left alone, so the page still renders -- one line short.
    """
    if name != "home.html":
        return source
    if HINT_ANCHOR not in source:
        logger.error("home.html has no sign-in field to patch; the NetID hint line "
                     "will not appear")
        return source
    return source.replace(HINT_ANCHOR, HINT_BLOCK, 1)


def install_template_patch(app) -> None:
    """Only `get_source` is overridden, so caching and auto-reload stay Jinja's."""
    from jinja2 import BaseLoader

    inner = app.jinja_loader

    class PatchingLoader(BaseLoader):
        def get_source(self, environment, template):
            source, filename, uptodate = inner.get_source(environment, template)
            return patch_template(template, source), filename, uptodate

        def list_templates(self):
            return inner.list_templates()

    app.jinja_loader = PatchingLoader()


# --- the views ----------------------------------------------------------------

def install(app, flask_server) -> None:
    """Validate the NetID on /auth, and turn off the two password endpoints."""
    import sys

    from flask import redirect, render_template, request, url_for

    routes = next((sys.modules[name] for name in ("routes", "potato.routes")
                   if name in sys.modules), None)
    if routes is None:                                  # pragma: no cover
        logger.error("routes module not loaded; NetID checking NOT installed")
        return

    config = flask_server.config
    original_auth = app.view_functions["auth"]

    def auth():
        """Potato's sign-in, with the NetID checked and normalised first.

        Wrapped rather than rewritten: everything after the check -- the
        auto-registration, the session, the phase advance, the redirect -- is
        Potato's and stays Potato's. The typed value is replaced on the form so
        the rest of the request sees the cleaned one.
        """
        if request.method != "POST":
            return original_auth()

        typed = request.form.get("email") or ""
        netid = clean_netid(typed)
        if not netid:
            logger.warning("Refused sign-in for %r: not a usable NetID", typed[:80])
            return render_template(
                "home.html",
                login_error=NETID_ERROR,
                title=config.get("annotation_task_name", "Annotation Platform"),
                require_password=config.get("require_password", True))

        if netid != typed.strip():
            logger.info("NetID %r normalised to %r", typed.strip()[:80], netid)

        # request.form is immutable; a mutable copy is the supported way to
        # hand the rest of the request a corrected value.
        form = request.form.copy()
        form["email"] = netid
        request.form = form
        return original_auth()

    def closed():
        """/register and /forgot-password, which this study does not have."""
        logger.info("Blocked %s: this study has no passwords", request.path)
        return redirect(url_for("home"))

    app.view_functions["auth"] = auth
    for endpoint in ("register", "forgot_password"):
        if endpoint in app.view_functions:
            app.view_functions[endpoint] = closed

    print("sign-in      NetID only, no password; /register and /forgot-password "
          "are closed")
