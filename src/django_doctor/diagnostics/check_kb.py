"""Plain-language explanations for common Django system check identifiers.

Django's own ``msg``/``hint`` are always shown; these entries add the "why"
and a concrete next step. Unknown ids simply get no extra text.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CheckAdvice:
    explanation: str
    fix: str


KNOWLEDGE_BASE: dict[str, CheckAdvice] = {
    # --- fields -----------------------------------------------------------
    "fields.E120": CheckAdvice("CharField needs a maximum length so the database column can be created.",
                               "Add max_length=... (or use TextField for unbounded text)."),
    "fields.E130": CheckAdvice("DecimalField needs max_digits.", "Add max_digits=... and decimal_places=..."),
    "fields.E131": CheckAdvice("DecimalField needs decimal_places.", "Add decimal_places=..."),
    "fields.E210": CheckAdvice("ImageField needs the Pillow library to validate images.", "pip install Pillow"),
    "fields.E300": CheckAdvice("A relation points to a model that is not installed or does not exist.",
                               "Check the 'app_label.ModelName' reference and that the app is in INSTALLED_APPS."),
    "fields.E304": CheckAdvice("Two relations create the same reverse accessor on the target model "
                               "(e.g. two ForeignKeys to User in one model).",
                               "Give each relation a distinct related_name=..."),
    "fields.E305": CheckAdvice("Two relations create the same reverse query name on the target model.",
                               "Give each relation a distinct related_name / related_query_name."),
    "fields.E307": CheckAdvice("A lazy reference ('app.Model') points to a model that could not be found.",
                               "Fix the string reference or install the app that defines the model."),
    "fields.E005": CheckAdvice("choices must be a list of (value, label) pairs.", "Fix the choices definition."),
    "fields.E004": CheckAdvice("choices must be an iterable (a list or tuple).", "Fix the choices definition."),
    "fields.E009": CheckAdvice("max_length is smaller than the longest value in choices.", "Increase max_length."),
    "fields.W340": CheckAdvice("null=True has no effect on ManyToManyField.", "Remove null=True."),
    "fields.W342": CheckAdvice("A ForeignKey with unique=True is the same as a OneToOneField.", "Use OneToOneField."),
    "fields.E180": CheckAdvice("The database backend does not support JSONField.", "Use a database with JSON support."),
    # --- models -----------------------------------------------------------
    "models.W042": CheckAdvice("The model gets an auto-created primary key but DEFAULT_AUTO_FIELD is not configured.",
                               "Set DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField' in settings "
                               "(or default_auto_field on the AppConfig). Changing it later creates migrations."),
    "models.E006": CheckAdvice("A field clashes with a field of the same name from a parent model.", "Rename one of the fields."),
    "models.E012": CheckAdvice("An index/constraint refers to a field that does not exist.", "Fix the field name in Meta."),
    "models.E015": CheckAdvice("Meta.ordering refers to a field that does not exist.", "Fix Meta.ordering."),
    "models.E028": CheckAdvice("Two models use the same db_table.", "Give each model a unique db_table."),
    "models.E034": CheckAdvice("An index name is too long.", "Shorten the index name (max 30 characters)."),
    # --- admin ------------------------------------------------------------
    "admin.E108": CheckAdvice("list_display refers to a field or method that does not exist on the model or admin.",
                              "Fix the name in list_display or add the method."),
    "admin.E116": CheckAdvice("list_filter refers to something that is not a field.", "Fix list_filter."),
    "admin.E035": CheckAdvice("readonly_fields refers to a missing field or method.", "Fix readonly_fields."),
    "admin.E403": CheckAdvice("The admin needs the DjangoTemplates backend.", "Add a DjangoTemplates entry to TEMPLATES."),
    "admin.E406": CheckAdvice("The admin needs django.contrib.messages.", "Add it to INSTALLED_APPS."),
    "admin.E408": CheckAdvice("The admin needs AuthenticationMiddleware.", "Add it to MIDDLEWARE."),
    "admin.E409": CheckAdvice("The admin needs MessageMiddleware.", "Add it to MIDDLEWARE."),
    "admin.E410": CheckAdvice("The admin needs SessionMiddleware.", "Add it to MIDDLEWARE."),
    # --- urls ----------------------------------------------------------------
    "urls.E004": CheckAdvice("urlpatterns must be a list of path()/re_path() entries.", "Fix the urlpatterns definition."),
    "urls.W002": CheckAdvice("A URL pattern starts with '/'; Django patterns must not start with a slash.",
                             "Remove the leading slash."),
    "urls.W005": CheckAdvice("A URL namespace is used more than once, so reversing may pick the wrong one.",
                             "Give each include() a unique namespace."),
    "urls.E007": CheckAdvice("A custom error handler has the wrong signature.", "Fix handler400/403/404/500 signatures."),
    # --- templates / static ---------------------------------------------------------
    "templates.E001": CheckAdvice("APP_DIRS=True cannot be combined with a custom 'loaders' option.",
                                  "Remove APP_DIRS or the loaders option."),
    "staticfiles.E002": CheckAdvice("STATIC_ROOT must not be one of the STATICFILES_DIRS.",
                                    "Use a separate folder for STATIC_ROOT (e.g. BASE_DIR / 'staticfiles')."),
    "staticfiles.W004": CheckAdvice("A directory listed in STATICFILES_DIRS does not exist.",
                                    "Create the directory or remove it from STATICFILES_DIRS."),
    # --- settings ----------------------------------------------------------------
    "4_0.E001": CheckAdvice("Since Django 4.0 every CSRF_TRUSTED_ORIGINS entry must include the scheme.",
                            "Use 'https://example.com' instead of 'example.com'."),
    "auth.E003": CheckAdvice("The custom user model's USERNAME_FIELD must be unique.", "Add unique=True to that field."),
    "caches.E001": CheckAdvice("CACHES must define a 'default' cache.", "Add a default cache."),
    # --- deployment (manage.py check --deploy) ---------------------------------------------------
    "security.W004": CheckAdvice("HSTS is not enabled.", "Set SECURE_HSTS_SECONDS once HTTPS works everywhere."),
    "security.W008": CheckAdvice("HTTP requests are not redirected to HTTPS.", "Set SECURE_SSL_REDIRECT = True (or redirect at the proxy)."),
    "security.W009": CheckAdvice("SECRET_KEY is short, low-entropy or the insecure default.",
                                 "Generate a new random key and load it from the environment."),
    "security.W012": CheckAdvice("Session cookies may be sent over HTTP.", "Set SESSION_COOKIE_SECURE = True."),
    "security.W016": CheckAdvice("CSRF cookies may be sent over HTTP.", "Set CSRF_COOKIE_SECURE = True."),
    "security.W018": CheckAdvice("DEBUG is True, which leaks detailed errors in production.", "Set DEBUG = False in production."),
    "security.W020": CheckAdvice("ALLOWED_HOSTS is empty, so Django rejects every request when DEBUG is False.",
                                 "List your domain(s) in ALLOWED_HOSTS."),
    "mysql.W002": CheckAdvice("MySQL strict mode is not enabled, so invalid data may be silently truncated.",
                              "Set 'init_command': \"SET sql_mode='STRICT_TRANS_TABLES'\" in OPTIONS."),
}


def advice_for(check_id: str | None) -> CheckAdvice | None:
    if not check_id:
        return None
    return KNOWLEDGE_BASE.get(check_id)
