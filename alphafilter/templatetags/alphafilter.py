from __future__ import absolute_import
import django
from django.utils.translation import ugettext as _
from django.template import (Library, Node, Variable, VariableDoesNotExist,
                            TemplateSyntaxError, RequestContext, Context)
from django.template.loader import get_template

from alphafilter.sql import FirstLetter

register = Library()


def _get_default_letters(model_admin=None):
    """
    Returns the set of letters defined in the configuration variable
    DEFAULT_ALPHABET. DEFAULT_ALPHABET can be a callable, string, tuple, or
    list and returns a set.

    If a ModelAdmin class is passed, it will look for a DEFAULT_ALPHABET
    attribute and use it instead.
    """
    from django.conf import settings
    import string
    default_ltrs = string.digits + string.ascii_uppercase
    default_letters = getattr(settings, 'DEFAULT_ALPHABET', default_ltrs)
    if model_admin and hasattr(model_admin, 'DEFAULT_ALPHABET'):
        default_letters = model_admin.DEFAULT_ALPHABET
    if callable(default_letters):
        return set(default_letters())
    elif isinstance(default_letters, unicode):
        return set([x for x in default_letters])
    elif isinstance(default_letters, str):
        return set([x for x in default_letters.decode('utf8')])
    elif isinstance(default_letters, (tuple, list)):
        return set(default_letters)


def _get_available_letters(field_name, queryset):
    """
    Makes a query to the database to return the first character of each
    value of the field and table passed in.

    Returns a set that represents the letters that exist in the database.
    """
    if django.VERSION[1] != 4:
        result = queryset.values(field_name).annotate(
            fl=FirstLetter(field_name)
            ).values('fl').distinct()
        return set([res['fl'] for res in result if res['fl'] is not None])
    else:
        from django.db import connection
        qn = connection.ops.quote_name
        db_table = queryset.model._meta.db_table
        sql = "SELECT DISTINCT UPPER(SUBSTR(%s, 1, 1)) as letter FROM %s" \
                    % (qn(field_name), qn(db_table))
        cursor = connection.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall() or ()
        return set([row[0] for row in rows if row[0] is not None])


def alphabet(cl):
    """
    The inclusion tag that renders the admin/alphabet.html template in the
    admin. Accepts a ChangeList object, which is custom to the admin.
    """
    if not getattr(cl.model_admin, 'alphabet_filter', False):
        return
    field_name = cl.model_admin.alphabet_filter
    alpha_field = '%s__istartswith' % field_name
    alpha_lookup = cl.params.get(alpha_field, '')
    link = lambda d: cl.get_query_string(d)

    letters_used = _get_available_letters(field_name, cl.model.objects.all())
    all_letters = list(_get_default_letters(cl.model_admin) | letters_used)
    all_letters.sort()

    choices = [{
        'link': link({alpha_field: letter}),
        'title': letter,
        'active': letter == alpha_lookup,
        'has_entries': letter in letters_used, } for letter in all_letters]
    all_letters = [{
        'link': cl.get_query_string(None, [alpha_field]),
        'title': _('All'),
        'active': '' == alpha_lookup,
        'has_entries': True
    }, ]
    return {'choices': all_letters + choices}
alphabet = register.inclusion_tag('admin/alphabet.html')(alphabet)


class AlphabetFilterNode(Node):
    """
    Provide a list of links for first characters on items in a queryset

    {% qs_alphabet_filter objects "lastname" "myapp/template.html" %}
    """
    def __init__(self, qset, field_name, page_url, filtered=None,
        template_name="alphafilter/alphabet.html", strip_params=None):
        self.qset = Variable(qset)
        self.field_name = Variable(field_name)
        self.page_url = Variable(page_url)
        self.template_name = Variable(template_name)
        self.filtered = filtered
        if strip_params is None:
            self.strip_params = []
        else:
            self.strip_params = strip_params.split(',')

    def render(self, context):
        try:
            qset = self.qset.resolve(context)
        except VariableDoesNotExist:
            raise TemplateSyntaxError("Can't resolve the queryset passed")
        try:
            field_name = self.field_name.resolve(context)
        except VariableDoesNotExist:
            field_name = self.field_name.var
        try:
            page_url = self.page_url.resolve(context)
            path_items = page_url.strip('/').split('/')
            page_url = path_items[0]
            alpha_lookup = ''
            if len(path_items) > 1:
                alpha_lookup = path_items[1]
        except VariableDoesNotExist:
            raise TemplateSyntaxError("Can't resolve the page_url passed")

        if not field_name:
            return ''

        request = context.get('request', None)

        if request is not None:
            qstring_items = request.GET.copy()
            for param in self.strip_params:
                if param in qstring_items:
                    qstring_items.pop(param)
            qstring = "&".join(["%s=%s" % (k, v) for k, v in qstring_items.iteritems()])
        else:
            qstring = ''

        link = lambda d: "/%s/%s%s" % (page_url,
                                       '%s/' % d.items()[0][1] if d.items()[0][1] != '' else '',
                                       '?%s' % qstring if qstring else '')
        if self.filtered == None:
            letters_used = _get_available_letters(field_name, qset)
        else:
            letters = [getattr(row, field_name)[0] for row in qset]
            if alpha_lookup == '' and letters is not None:
                alpha_lookup = letters[0]
            letters_used = set(letters)

        all_letters = list(_get_default_letters(None) | letters_used)
        all_letters.sort()

        choices = [{
            'link': link({'alphabet': letter.lower()}),
            'title': letter,
            'active': letter.lower() == alpha_lookup,
            'has_entries': letter in letters_used, } for letter in all_letters if not letter.isnumeric()]

        has_digits = False
        for letter in all_letters:
            if letter.isnumeric():
                has_digits = True
                break
        digits = [{
            'link': link({'alphabet': '0-9'}),
            'title': _('0-9'),
            'active': '0-9' == alpha_lookup,
            'has_entries': has_digits
        }, ]
        all_letters = [{
            'link': link({'alphabet': ''}),
            'title': _('All'),
            'active': '' == alpha_lookup,
            'has_entries': True
        }, ]
        ctxt = {'choices': all_letters + digits + choices, 'page_url': page_url}

        tmpl = get_template(self.template_name)

        if request is not None:
            return tmpl.render(RequestContext(request, ctxt))
        else:
            return tmpl.render(Context(ctxt))


@register.tag
def qs_alphabet_filter(parser, token):
    """
    The parser/tokenizer for the queryset alphabet filter.

    {% qs_alphabet_filter <queryset> <field name> [<template name>] [strip_params=comma,delim,list] %}

    {% qs_alphabet_filter objects lastname myapp/template.html %}

    The template name is optional and uses alphafilter/alphabet.html if not
    specified
    """
    bits = token.split_contents()
    if len(bits) == 4:
        return AlphabetFilterNode(bits[1], bits[2], bits[3])
    elif len(bits) == 5:
        if "=" in bits[4]:
            key, val = bits[4].split('=')
            return AlphabetFilterNode(bits[1], bits[2], bits[3], strip_params=val)
        else:
            return AlphabetFilterNode(bits[1], bits[2], bits[3], template_name=bits[4])
    elif len(bits) == 6:
        key, val = bits[5].split('=')
        return AlphabetFilterNode(bits[1], bits[2], bits[3], bits[4], bits[5])
    else:
        raise TemplateSyntaxError("%s is called with a queryset, field "
            "name and base page url, and optionally a template." % bits[0])
