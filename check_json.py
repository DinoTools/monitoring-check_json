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
from html.parser import HTMLParser
import logging
import json
from pathlib import Path
from pprint import pformat
import sys
import textwrap
from typing import Any, Dict
import urllib3

import jmespath
import nagiosplugin
import requests
import requests.adapters
import requests.cookies

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


class SelectiveTableParser(HTMLParser):
    def __init__(self, target_id=None, target_class=None):
        super().__init__()
        self.target_id = target_id
        self.target_class = target_class

        self.rows = []
        self.current_row = []
        self.current_cell = ""

        self.in_target_table = False
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        if tag == 'table':
            id_match = not self.target_id or attrs_dict.get('id') == self.target_id
            class_list = attrs_dict.get("class", "").split()
            class_match = not self.target_class or self.target_class in class_list

            if id_match and class_match:
                self.in_target_table = True

        if self.in_target_table:
            if tag in ('td', 'th'):
                self.in_cell = True
                self.current_cell = ""
            elif tag == 'tr':
                self.current_row = []

    def handle_data(self, data):
        if self.in_target_table and self.in_cell:
            self.current_cell += data.strip()

    def handle_endtag(self, tag):
        if tag == 'table' and self.in_target_table:
            self.in_target_table = False
        elif self.in_target_table:
            if tag in ('td', 'th'):
                self.current_row.append(self.current_cell.strip())
                self.in_cell = False
            elif tag == 'tr':
                if self.current_row:
                    self.rows.append(self.current_row)


def save_cookies(session: requests.Session, filename: Path):
    logger.debug(f"Saving cookie date to: {filename}")
    with filename.open("w") as f:
        cookie_dict = requests.utils.dict_from_cookiejar(session.cookies)
        json.dump(cookie_dict, f)


def table_to_json(html_content, table_id=None, table_class=None, table_key_index=0, table_value_index=1):
    parser = SelectiveTableParser(target_id=table_id, target_class=table_class)
    parser.feed(html_content)
    results = {}
    for row in parser.rows:
        results[row[table_key_index]] = row[table_value_index]

    return results


def load_cookies(session: requests.Session, filename: Path):
    if filename.exists():
        logger.debug(f"Loading cookie date from: {filename}")
        with filename.open("r") as f:
            try:
                cookie_dict = json.load(f)
                session.cookies.update(requests.utils.cookiejar_from_dict(cookie_dict))
            except json.JSONDecodeError:
                pass


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
        "--cookie-file",
        dest="cookie_file",
        default=None,
        help="Path to save/load cookies",
    )

    argp.add_argument(
        "--login-url",
        dest="login_url",
        help="URL to perform a login (optional)",
    )
    argp.add_argument(
        "--login-check-url",
        dest="login_check_url",
        help="URL to check if we are logged in (optional)",
    )
    argp.add_argument(
        "--username",
        dest="username",
        help="Login username",
    )
    argp.add_argument(
        "--password",
        dest="password",
        help="Login password",
    )
    argp.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="Timeout in seconds (Default: 10)",
    )
    argp.add_argument(
        "--insecure",
        action="store_false",
        dest="verify",
        default=True,
        help="Ignore SSL certificate",
    )

    argp.add_argument(
        "--parse-html-table",
        action="store_true",
        dest="parse_html_table",
        default=False,
        help="If the API returns a HTML table with value, we can parse it",
    )
    argp.add_argument(
        "--select-html-table-id",
        dest="html_table_id",
        help=""
    )
    argp.add_argument(
        "--select-html-table-class",
        dest="html_table_class",
        help=""
    )
    argp.add_argument(
        "--html-table-key-index",
        dest="html_table_key_index",
        type=int,
        default=0,
        help=""
    )
    argp.add_argument(
        "--html-table-value-index",
        dest="html_table_value_index",
        type=int,
        default=1,
        help=""
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

    cookie_file: None | Path = None
    if args.cookie_file:
        cookie_file = Path(args.cookie_file)

    if args.url:
        req_session = requests.Session()
        req_session.cookies = requests.cookies.RequestsCookieJar()
        req_session.verify = args.verify
        # ToDo: enable if in debug mode
        urllib3.disable_warnings()

        if cookie_file:
            load_cookies(req_session, cookie_file)

        login_check_successfull = None
        if args.login_check_url:
            logger.debug("Performing login check ...")
            login_check_res = req_session.get(args.login_check_url, timeout=args.timeout)
            if login_check_res.status_code == 200:
                login_check_successfull = True
            else:
                login_check_successfull = False
            logger.debug(f"Login check was {'' if login_check_successfull else 'not '}successfull")

        if not login_check_successfull and args.login_url:
            logger.debug("Performing login ...")
            auth = (args.username, args.password) if args.username else None
            login_res = req_session.get(args.login_url, auth=auth, timeout=args.timeout)
            if login_res.status_code != 200:
                logger.debug(
                    "Login not successfull. "
                    f"code={login_res.status_code} "
                    f"reason={login_res.reason}"
                )
                check.results.add(
                    nagiosplugin.Result(
                        state=nagiosplugin.Unknown,
                        hint="Unable to login"
                    )
                )
                check.main(verbose=args.verbose)
            logger.debug("Login successfull")

        logger.debug(f"Fetching data from {args.url}")
        res = req_session.get(args.url, timeout=args.timeout)
        if args.parse_html_table:
            logger.debug("Converting table to JSON")
            data = table_to_json(
                res.text,
                table_id=args.html_table_id,
                table_class=args.html_table_class,
                table_key_index=args.html_table_key_index,
                table_value_index=args.html_table_value_index,
            )
        else:
            logger.debug("Parsing response as JSON")
            data = res.json(parse_float=decimal.Decimal)

        if cookie_file:
            save_cookies(req_session, cookie_file)
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
