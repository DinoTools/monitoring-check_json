#!/usr/bin/env python3
# SPDX-FileCopyrightText: PhiBo DinoTools (2022)
# SPDX-License-Identifier: GPL-3.0-or-later
"""
This is a monitoring plugin to check JSON APIs and files.
It uses the JMESPath query language to extract the data.

For more information have a look at https://jmespath.org/

Examples:

{cmd_name} --file examples/simple_dict.json --check-value "level;level;1;2"

{cmd_name} --file examples/advanced_dict.json --check-text-ok "first_status;results[?component=='first'].status;ok"

{cmd_name} --file examples/advanced_dict.json --check-text-ok "second_status;results[?component=='second'].status;ok"
"""

import argparse
import decimal
import logging
import json
from pprint import pformat
import sys
import textwrap
from typing import Any, Dict

import jmespath
import nagiosplugin
import requests
import requests.adapters

logger = logging.getLogger("nagiosplugin")


class NumericValue(nagiosplugin.Resource):
    name = "JSON API"

    def __init__(self, data, value_queries: Dict[str, str], value_params: Dict[str, Dict[str, Any]]):
        super().__init__()

        self._data = data
        self._value_queries = value_queries
        self._value_params = value_params

    def probe(self):
        for name, expression in self._value_queries.items():
            logger.debug(f"Extracting value '{name}' with query '{expression}'")
            value_raw = jmespath.search(expression, self._data)

            value = None
            if isinstance(value_raw, list) and len(value_raw) > 0:
                value_raw = value_raw[0]

            from decimal import Decimal
            if isinstance(value_raw, str):
                value = Decimal(value_raw)
            elif isinstance(value_raw, (int, float, decimal.Decimal)):
                value = value_raw
            else:
                yield nagiosplugin.Result(
                    state=nagiosplugin.Unknown,
                    hint=f"Extracted value for {name} has type {type(value_raw)} expected str"
                )
                continue

            logger.debug(f"Found '{name}'='{value}'")
            yield nagiosplugin.Metric(
                name=name,
                value=value,
                **self._value_params[name]
            )


@nagiosplugin.guarded()
def main():
    argp = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            __doc__.format(cmd_name=sys.argv[0])
        )
    )
    argp.add_argument(
        "--url",
        dest="url",
        help="URL of the JSON API",
        metavar="URL",
    )

    argp.add_argument(
        "--file",
        dest="filename",
        help="JSON File",
        metavar="FILE",
    )

    argp.add_argument(
        "--base",
        dest="base_selector",
        default=None,
        help="",
    )

    argp.add_argument(
        "--check-value",
        dest="check_values",
        help="<name/label>;<value filter/query>[;<warning>[;<critical>[;<min>[;<max>[;<uom>]]]]]]",
        action="append",
        default=[],
    )

    argp.add_argument(
        "--check-text-ok",
        dest="check_text_oks",
        help="<name/label>;<value filter/query>;<expected value>",
        action="append",
        default=[],
    )

    argp.add_argument('-v', '--verbose', action='count', default=0)
    args = argp.parse_args()

    runtime = nagiosplugin.Runtime()
    runtime.verbose = args.verbose

    check = nagiosplugin.Check()

    if args.url:
        req_session = requests.Session()
        res = req_session.get(args.url)
        data = res.json(parse_float=decimal.Decimal)
    elif args.filename:
        data = json.load(open(args.filename, "r"))
    else:
        check.results.add(
            nagiosplugin.Result(
                state=nagiosplugin.Unknown,
                hint="You have to provide an url or filename"
            )
        )
        return check.main(args.verbose)

    logger.debug("Got data: " + pformat(data))
    if args.base_selector is not None:
        logger.info(f"Using base selector '{args.base_selector}'")
        data = jmespath.search(args.base_selector, data)
        logger.debug("Using base selector to extract data: " + pformat(data))

    check.results.add(
        nagiosplugin.Result(
            state=nagiosplugin.Ok,
            hint="Everything looks good"
        )
    )

    value_queries: Dict[str, str] = {}
    value_params: Dict[str, Dict[str, Any]] = {}
    for check_value in args.check_values:
        check_value_split = check_value.split(";")

        if len(check_value_split) < 2:
            check.results.add(
                nagiosplugin.Result(
                    state=nagiosplugin.Unknown,
                    hint=(
                        "Parameter for --check-value must have at least 2 values separated by ';'. "
                        f"Found {len(check_value_split)}"
                    )
                )
            )
            continue

        check_value_name, \
            check_value_query, \
            check_value_warning, \
            check_value_critical, \
            check_value_min, \
            check_value_max, \
            check_value_uom, \
            *_ = check_value_split + [""] * 5

        value_queries[check_value_name] = check_value_query
        value_params[check_value_name] = {
            "min": check_value_min,
            "max": check_value_max,
            "uom": check_value_uom,
        }

        check.add(
            nagiosplugin.ScalarContext(
                name=check_value_name,
                warning=check_value_warning,
                critical=check_value_critical,
            )
        )

    if len(value_queries) > 0:
        check.add(NumericValue(data=data, value_queries=value_queries, value_params=value_params))

    for check_text_ok in args.check_text_oks:
        """
        """

        check_text_ok_split = check_text_ok.split(";")

        if len(check_text_ok_split) < 3:
            check.results.add(
                nagiosplugin.Result(
                    state=nagiosplugin.Unknown,
                    hint=(
                        "Parameter for --check-text-ok must have at least 3 values separated by ';'. "
                        f"Found {len(check_text_ok_split)}"
                    )
                )
            )
            continue

        check_text_ok_name, \
            check_text_ok_filter, \
            check_text_ok_expected_value = check_text_ok_split

        check_text_ok_value_raw = jmespath.search(check_text_ok_filter, data)

        check_text_ok_value = None
        if isinstance(check_text_ok_value_raw, str):
            check_text_ok_value = check_text_ok_value_raw
        elif isinstance(check_text_ok_value_raw, list) and len(check_text_ok_value_raw) > 0:
            check_text_ok_value = check_text_ok_value_raw[0]

        if not isinstance(check_text_ok_value, str):
            check.results.add(
                nagiosplugin.Result(
                    state=nagiosplugin.Unknown,
                    hint=f"Extracted value for {check_text_ok_name} has type {type(check_text_ok_value)} expected str"
                )
            )
            continue

        if check_text_ok_value == check_text_ok_expected_value:
            check.results.add(
                nagiosplugin.Result(
                    state=nagiosplugin.Ok,
                )
            )
        else:
            check.results.add(
                nagiosplugin.Result(
                    state=nagiosplugin.Warn,
                    hint=(
                        f"Expected {check_text_ok_name} to be '{check_text_ok_expected_value}' "
                        f"but is '{check_text_ok_value}'"
                    )
                )
            )

    check.main(verbose=args.verbose)


if __name__ == "__main__":
    main()
