check_json
==========

This is a monitoring plugin for [Icinga](https://icinga.com/), [Nagios](https://www.nagios.org/) and other compatible
monitoring solutions to check JSON APIs and files. It uses the JMESPath query language to extract the data.

For more information have a look at https://jmespath.org/

Requirements
------------

- Python 3.6+
  - jmespath
  - nagiosplugin
  - requests


Examples
--------

### Check JSON file

```
./check_json.py --file examples/simple_dict.json --check-value "level;level;1;2"
```

```
./check_json.py --file examples/advanced_dict.json --check-text-ok "first_status;results[?component=='first'].status;ok"
```

```
./check_json.py --file examples/advanced_dict.json --check-text-ok "second_status;results[?component=='second'].status;ok"
```

### Check JSON from URL

```
./check_json.py --url https://example.org/your/json/service --check-value "level;level;1;2"
```

Tip
---

Use the verbose output to debug if the plugin is unablte to extract the right values.

```
./check_json.py -vvv ....
```


Resources
---------

- Git-Repository: https://github.com/DinoTools/monitoring-check_json-api
- Issues: https://github.com/DinoTools/monitoring-check_json-api/issues

License
-------

GPLv3+
