#!/bin/bash -e

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "${SCRIPT_DIR}/.."

cd src
./build.sh
cd ..

python3 -m venv /tmp/cdbg-venv
source /tmp/cdbg-venv/bin/activate
pip3 install -r requirements.txt
pip3 install src/dist/* --force-reinstall

cd firebase-sample
pip3 install -r requirements.txt
python3 -m flask run
cd ..

deactivate
