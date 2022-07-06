#!/bin/bash -e

cd src
./build.sh
cd ..

python3 -m venv /tmp/cdbg-venv
source /tmp/cdbg-venv/bin/activate
pip3 install -r requirements_dev.txt
pip3 install src/dist/* --force-reinstall

cd firebase-test
pip3 install -r requirements.txt
python3 -m flask run
cd ..

deactivate
