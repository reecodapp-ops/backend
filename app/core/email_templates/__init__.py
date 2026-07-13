"""HTML email templates rendered by EmailService.

Each template is a small, self-contained string with a single inline style
block — kept inline because mail clients strip <link rel="stylesheet">.
Templates take a dict of context and use Python str.format() so there is no
hard dependency on Jinja2 or another templating library.

If/when richer logic is needed, swap in jinja2.Environment(loader=PackageLoader)
without changing the EmailService API.
"""
