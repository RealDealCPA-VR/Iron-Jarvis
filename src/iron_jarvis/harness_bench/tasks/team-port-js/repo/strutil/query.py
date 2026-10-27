"""parse_query: the port of parseQuery in reference/strutil.js."""


def parse_query(query):
    """The parameters of a query string: parse_query("?a=1&b=x+y") -> {"a": "1", "b": "x y"}.
    A name given more than once, or written "name[]", collects its values in a list."""
    raise NotImplementedError("port parseQuery from reference/strutil.js")
