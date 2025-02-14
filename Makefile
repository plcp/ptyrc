
venv/bin/activate:
	virtualenv venv
	source venv/bin/activate && pip install -r requirements.txt

requirements: venv/bin/activate
	source venv/bin/activate && pip install -r requirements.txt

venv: venv/bin/activate

format:
	black ptyrc/*.py
	isort --profile black ptyrc/*.py

install:
	pip install .
