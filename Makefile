PYTHON := $(wildcard .venv/bin/python)
ifeq ($(PYTHON),)
PYTHON := python3
endif

.PHONY: test verify

test:
	cd $(dir $(lastword $(MAKEFILE_LIST))) && $(PYTHON) -m unittest discover -s tests -v

# Runs each content/verify/<qid>/ program against outcome.json (10s timeout)
verify:
	cd $(dir $(lastword $(MAKEFILE_LIST))) && $(PYTHON) app/verify.py
