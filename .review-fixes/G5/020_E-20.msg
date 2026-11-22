fix(ui): escape remaining raw interpolations in innerHTML templates

A few templates still interpolated config/server values unescaped: the
sidebar row's video index, eight numeric value="" fields of the provider
modal, and schema select_or_number options plus number inputs in the
settings form. A string in YAML could break out of the attribute. Wrap
them in escapeHtml(); the html-tag-template/ESLint migration is left for
a follow-up. Regression test for the row label added.

Refs: E-20
