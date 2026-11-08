# SYNTHETIC: MUST Company hiring Quest. All data is generated, not real.
PY ?= python

.PHONY: all setup data qa verify evidence test clean

all: data qa verify evidence test

setup:
	$(PY) -m pip install -r requirements.txt

data:
	$(PY) data/generate_data.py

qa:
	$(PY) qa_check.py --pings data/pings.csv --out reports/

verify:
	$(PY) verify_tool.py --validation reports/validation.csv --manifest data/fault_manifest.csv --out reports/

evidence:
	$(PY) docs/make_figures.py > reports/make_figures_output.txt
	$(PY) docs/findings_numbers.py > reports/findings_numbers.txt

test:
	$(PY) -m pytest --junitxml=reports/test-results.xml
	$(PY) tests/mutation_check.py
	$(PY) tests/summarize_results.py reports/test-results.xml reports/test-summary.md

clean:
	rm -f reports/*.csv reports/*.html reports/*.xml reports/*.md reports/*.txt
