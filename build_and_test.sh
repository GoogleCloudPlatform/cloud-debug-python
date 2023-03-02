#!/bin/bash -e

# Clean up any previous generated test files.
rm -rf tests/py/__pycache__

cd src
./build.sh
cd ..

python3 -m venv /tmp/cdbg-venv
source /tmp/cdbg-venv/bin/activate
pip3 install -r requirements_dev.txt
pip3 install src/dist/* --force-reinstall
python3 -m pytest tests/py
deactivate

# Clean up any generated test files.
rm -rf tests/py/__pycache__
