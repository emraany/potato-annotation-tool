#!/usr/bin/env python3
"""
Account security for the study: registration that validates, and a password
reset that goes to the annotator's inbox instead of the screen.

WHAT WAS WRONG.  Potato 2.9.3's `/forgot-password` mints a real reset token for
any username typed into it and then **renders the link on the page**
(`routes.py:9485`). The comment beside it says the response is uniform "to
prevent user enumeration", which it is -- while handing the reader a working
password reset for somebody else's account. Type a colleague's username, follow
the link, set their password, sign in as them. That is account takeover with no
credential at all, and it is the default behaviour of the deployed study.

Registration had its own gaps: one field and a password of any length, no
second entry to catch a typo, no email anywhere -- so a forgotten password had
nowhere to be sent even if the flow had wanted to send it -- and a username
used verbatim as a directory name (`user_state_management.py:1045`), which an
open study makes anybody's to choose.

WHAT THIS DOES.  Replaces the two view functions and patches the register form
in Potato's template, rather than forking either. Registration takes a
username, an email, a password and a confirmation, and checks all four. Reset
takes a username or an email, and mails the link to the address on the account
-- the page says the same neutral sentence either way and never shows a link.
No SMTP configured means no reset email is sent and none is displayed: it fails
closed, and says to contact the administrator.

The token machinery underneath is Potato's and is sound: 32 bytes from
`secrets`, 24-hour expiry, single use, and a new token invalidates the old one
(`authentication.py:757`). Tokens live in memory, so a redeploy cancels the
ones outstanding -- a person mid-reset asks again.

SMTP is configured with five environment variables. Nothing here is
provider-specific; anything that speaks SMTP works.

    SMTP_HOST       smtp.gmail.com
    SMTP_PORT       587
    SMTP_USER       the mailbox to authenticate as
    SMTP_PASSWORD   its password, or an app password
    SMTP_FROM       the From: address (defaults to SMTP_USER)
"""

from __future__ import annotations

import logging
import os
import re
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)

MIN_PASSWORD_CHARS = 6

# A username is also a directory name -- annotation_output/<username>/ -- and
# in an open study anybody chooses their own. Restricting the alphabet is what
# stops "../.." being a username, and keeps a name that cannot be typed back
# out of the export.
USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{1,38})[A-Za-z0-9]$")

# Deliberately loose. The point is to catch a typo and a missing @, not to
# adjudicate what is a legal address -- the only test that really matters is
# whether the reset mail arrives.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$")

RESET_SUBJECT = "Reset your annotation study password"

RESET_BODY = """\
Somebody asked to reset the password for the annotation study account "{username}".

Open this link to choose a new one. It works once and expires in 24 hours:

{link}

If that was not you, ignore this message -- nothing has changed and the link
cannot be used without opening it.
"""


# --- email --------------------------------------------------------------------

def smtp_settings() -> dict | None:
    """The SMTP configuration, or None when it is not set up."""
    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    if not (host and user and password):
        return None
    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": user,
        "password": password,
        "sender": os.environ.get("SMTP_FROM", "").strip() or user,
    }


def send_reset_email(to_address: str, username: str, link: str) -> bool:
    """Mail one reset link. False on any failure, and the reason is logged.

    The caller must not tell the browser which it was: whether an address is on
    file is exactly what a stranger typing somebody else's username is trying
    to learn.
    """
    settings = smtp_settings()
    if settings is None:
        logger.warning(
            "Password reset requested for '%s' but SMTP is not configured "
            "(SMTP_HOST / SMTP_USER / SMTP_PASSWORD); no mail sent and no link "
            "shown. Mint one deliberately from /admin/create_reset_token.",
            username)
        return False

    message = EmailMessage()
    message["Subject"] = RESET_SUBJECT
    message["From"] = settings["sender"]
    message["To"] = to_address
    message.set_content(RESET_BODY.format(username=username, link=link))

    try:
        context = ssl.create_default_context()
        if settings["port"] == 465:
            with smtplib.SMTP_SSL(settings["host"], settings["port"],
                                  context=context, timeout=20) as server:
                server.login(settings["user"], settings["password"])
                server.send_message(message)
        else:
            with smtplib.SMTP(settings["host"], settings["port"], timeout=20) as server:
                server.starttls(context=context)
                server.login(settings["user"], settings["password"])
                server.send_message(message)
    except Exception as exc:          # noqa: BLE001 - a reset must never 500
        logger.error("Could not send the reset email for '%s': %s: %s",
                     username, type(exc).__name__, exc)
        return False

    logger.info("Sent a password reset link for '%s'", username)
    return True


# --- the template -------------------------------------------------------------

REGISTER_BLOCK_ANCHOR = '<div class="tab-content" id="register-content">'

REGISTER_BLOCK = """<div class="tab-content" id="register-content">
                <form method="POST" action="{{ url_prefix }}/register">
                    <input type="hidden" name="action" value="signup">
                    <div class="potato-form-group">
                        <label for="register-email" class="potato-form-label">Username</label>
                        <input id="register-email" name="username" type="text" class="potato-form-input"
                               placeholder="Choose a username (not an email address)"
                               autocomplete="username" required>
                    </div>
                    <div class="potato-form-group">
                        <label for="register-address" class="potato-form-label">Email</label>
                        <input id="register-address" name="email" type="email" class="potato-form-input"
                               placeholder="Where a password reset would be sent"
                               autocomplete="email" required>
                    </div>
                    <div class="potato-form-group">
                        <label for="register-pass" class="potato-form-label">Password</label>
                        <input id="register-pass" name="pass" type="password" class="potato-form-input"
                               placeholder="At least 6 characters" minlength="6"
                               autocomplete="new-password" required>
                    </div>
                    <div class="potato-form-group">
                        <label for="register-pass2" class="potato-form-label">Confirm password</label>
                        <input id="register-pass2" name="pass_confirm" type="password" class="potato-form-input"
                               placeholder="Type it again" minlength="6"
                               autocomplete="new-password" required>
                    </div>
                    <button type="submit" class="potato-form-submit">Register and start annotating</button>
                </form>
            </div>"""

# forgot_password.html says "a reset link has been generated", which was true
# of a page that printed one. It is now sent, or not, and either way the reader
# is told the same thing.
FORGOT_OLD = "If the username exists, a reset link has been generated."
FORGOT_NEW = ("If that account exists and has an email address on file, a reset "
              "link is on its way. It expires in 24 hours.")

FORGOT_LABEL_OLD = ('<label for="username" class="potato-form-label">Username</label>')
FORGOT_LABEL_NEW = ('<label for="username" class="potato-form-label">Username or email</label>')

FORGOT_PLACEHOLDER_OLD = 'placeholder="Enter your username"'
FORGOT_PLACEHOLDER_NEW = 'placeholder="Your username, or the email you registered with"'


def patch_template(name: str, source: str) -> str:
    """Rewrite one of Potato's templates as it is loaded.

    A replacement of the block rather than a copy of the file: everything else
    in home.html -- the OAuth branch, the ui_language strings, the layout --
    stays Potato's and keeps following it. A missing anchor is reported and
    left alone, which degrades to Potato's own form rather than a blank page.
    """
    if name == "home.html":
        if REGISTER_BLOCK_ANCHOR not in source:
            logger.error("home.html has no register block to patch; the form is "
                         "Potato's own and will not ask for an email")
            return source
        start = source.index(REGISTER_BLOCK_ANCHOR)
        end = source.index("</div>", source.index("</form>", start)) + len("</div>")
        return source[:start] + REGISTER_BLOCK + source[end:]

    if name == "forgot_password.html":
        for old, new in ((FORGOT_OLD, FORGOT_NEW),
                         (FORGOT_LABEL_OLD, FORGOT_LABEL_NEW),
                         (FORGOT_PLACEHOLDER_OLD, FORGOT_PLACEHOLDER_NEW)):
            if old in source:
                source = source.replace(old, new, 1)
        return source

    return source


def install_template_patch(app) -> None:
    """Rewrite templates as Jinja loads them, keeping Potato's loader beneath.

    Only `get_source` is overridden, so caching, auto-reload and every other
    loader behaviour stays Jinja's own.
    """
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
    """Replace /register and /forgot-password on a built app."""
    from flask import redirect, render_template, request, session, url_for
    from potato.authentication import UserAuthenticator

    import sys
    routes = next((sys.modules[name] for name in ("routes", "potato.routes")
                   if name in sys.modules), None)
    if routes is None:                                  # pragma: no cover
        logger.error("routes module not loaded; account hardening NOT installed")
        return

    config = flask_server.config

    def title() -> str:
        return config.get("annotation_task_name", "Annotation Platform")

    def refuse(message: str):
        return render_template("home.html", title=title(), login_error=message)

    def register():
        """Username, email, password, confirmation -- all four checked."""
        if "username" in session:
            return routes.home()

        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("pass") or ""
        confirm = request.form.get("pass_confirm") or ""

        if not (username and email and password and confirm):
            return refuse("Username, email, password and confirmation are all required.")
        if not USERNAME_RE.match(username):
            return refuse("A username is 3 to 40 characters, letters, digits, dots, "
                          "dashes and underscores, starting and ending with a letter "
                          "or a digit.")
        if not EMAIL_RE.match(email):
            return refuse("That does not look like an email address.")
        if len(password) < MIN_PASSWORD_CHARS:
            return refuse(f"The password needs at least {MIN_PASSWORD_CHARS} characters.")
        if password != confirm:
            return refuse("The two passwords are not the same.")

        authenticator = UserAuthenticator.get_instance()

        # Potato's guard, kept: a username that already owns a directory but no
        # account means the roster and the output have gone out of step, and
        # registering would hand a stranger somebody else's annotations.
        if routes._username_owns_annotations(username):
            logger.error("Refused registration for '%s': the directory exists but the "
                         "account does not; restore the roster file", username)
            return refuse("That username is already in use on this task. "
                          "Ask the study administrator to restore your account.")

        # One address, one account: the address is how a reset finds its way
        # back, and two accounts sharing one inbox makes that ambiguous.
        existing = getattr(authenticator, "users", {}) or {}
        for other, data in existing.items():
            if isinstance(data, dict) and \
                    (data.get("email") or "").strip().casefold() == email.casefold():
                logger.info("Refused registration for '%s': email already on '%s'",
                            username, other)
                return refuse("That email address is already registered. "
                              "Sign in, or use the reset link if you have "
                              "forgotten the password.")

        result = authenticator.add_user(username, password, email=email)
        if result != "Success":
            logger.warning("Registration failed for '%s': %s", username, result)
            return refuse("That username is taken." if result == "Duplicate user" else result)

        authenticator.save_user_config()

        session["username"] = username
        session.permanent = True

        state_manager = routes.get_user_state_manager()
        if not state_manager.has_user(username):
            routes.init_user_state(username)
        user_state = state_manager.get_user_state(username)
        if user_state and user_state.get_phase() == routes.UserPhase.LOGIN:
            state_manager.advance_phase(username)
        if user_state and not user_state.has_assignments():
            routes.get_item_state_manager().assign_instances_to_user(user_state)

        logger.info("Registered '%s'", username)
        return redirect(url_for("home"))

    def forgot_password():
        """Mail the link. Never show it, never say whether the account exists."""
        if request.method == "GET":
            return render_template("forgot_password.html", title=title())

        typed = (request.form.get("username") or "").strip()
        if not typed:
            return render_template("forgot_password.html", title=title(),
                                   error="Enter your username or your email address.")

        authenticator = UserAuthenticator.get_instance()
        users = getattr(authenticator, "users", {}) or {}

        username = None
        if typed in users:
            username = typed
        else:
            for name, data in users.items():
                if isinstance(data, dict) and \
                        (data.get("email") or "").strip().casefold() == typed.casefold():
                    username = name
                    break

        # Everything below renders the same page. Whether the account exists,
        # whether it has an address, and whether the mail server accepted it are
        # all invisible to the browser and all in the log.
        if username is None:
            logger.info("Password reset asked for '%s', which is not an account", typed)
        else:
            address = (users.get(username, {}) or {}).get("email", "").strip()
            if not address:
                logger.warning("Password reset asked for '%s', which has no email on "
                               "file (registered before the email field existed); "
                               "mint one from /admin/create_reset_token", username)
            else:
                token = authenticator.create_reset_token(username)
                if token:
                    link = f"{request.host_url.rstrip('/')}/reset/{token}"
                    send_reset_email(address, username, link)

        return render_template("forgot_password.html", title=title(), success=True)

    app.view_functions["register"] = register
    app.view_functions["forgot_password"] = forgot_password

    configured = smtp_settings() is not None
    print("accounts     register asks for username, email, password and confirmation; "
          f"password floor {MIN_PASSWORD_CHARS} characters")
    print("reset        " + ("emailed via " + os.environ["SMTP_HOST"] if configured else
                             "SMTP NOT CONFIGURED - no reset email is sent and no link "
                             "is shown (fails closed)"))
