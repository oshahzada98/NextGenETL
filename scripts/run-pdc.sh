#!/usr/bin/env bash
source ~/setEnvVars.sh

export MY_VENV=~/virtualEnvETL
export PYTHONPATH=.:${MY_VENV}/lib:~/extlib

mkdir -p ~/config
pushd ~/config > /dev/null
gsutil cp gs://${CONFIG_BUCKET}/${CURRENT_CONFIG_PATH}/PDCTablesBuild.yaml .
popd > /dev/null

pushd ${MY_VENV} > /dev/null
source bin/activate
popd > /dev/null

mkdir -p ~/scratch

cd ..
python3 ./BQ_Table_Building/build_pdc_tables.py ~/config/PDCTablesBuild.yaml
deactivate
