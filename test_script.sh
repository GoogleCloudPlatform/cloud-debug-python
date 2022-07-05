#!/bin/bash -e

cd src
./build.sh
cd ..

python3 -m venv /tmp/cdbg-venv
source /tmp/cdbg-venv/bin/activate
pip3 install -r requirements_dev.txt
pip3 install src/dist/* --force-reinstall
pip3 install -r firebase-test/requirements.txt
python3 firebase-test/app.py
deactivate
