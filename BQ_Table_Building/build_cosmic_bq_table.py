"""

Copyright 2019, Institute for Systems Biology

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

"""

'''
Make sure the VM has BigQuery and Storage Read/Write permissions!
'''


import sys
import os
from os.path import expanduser
import yaml
import io
import requests
import string
from git import Repo
from json import loads as json_loads
from createSchemaP3 import build_schema

from common_etl.support import create_clean_target, bucket_to_local

'''
----------------------------------------------------------------------------------------------
The configuration reader. Parses the YAML configuration into dictionaries
'''
def load_config(yaml_config):
    yaml_dict = None
    config_stream = io.StringIO(yaml_config)
    try:
        yaml_dict = yaml.load(config_stream, Loader=yaml.FullLoader)
    except yaml.YAMLError as ex:
        print(ex)

    if yaml_dict is None:
        return None, None, None

    return yaml_dict['files_and_buckets_and_tables'], yaml_dict['steps']


'''
----------------------------------------------------------------------------------------------
Build Pull List from TXT
'''

def build_pull_list_from_txt(bucket, bucket_file, local_file, version):
    # move the txt file from the bucket to the VM
    success = bucket_to_local(bucket, bucket_file, local_file)
    if not success:
        return False
    # open the file for reading
    links = open(local_file, 'r').read().strip().split('\n')
    # create a list of the files in the file list
    all_filenames = [(y.split('?')[0]).strip(string.punctuation) for y in [x.split(version + '/')[1] for x in links]]
    filenames = []
    for i in len(all_filenames):
        base_file, ext2 = os.path.splitext(all_filenames[i])
        if ext_zip != ".gz":
            file, ext1 = os.path.splitext(base_file)
            # Check if tsv, add to files
            if  ext1 == ".tsv":

            # Check if csv, add to files

            # ignore all else





'''
----------------------------------------------------------------------------------------------
Download files
'''

def pull_from_aws():
    for link in len(links):

        response = requests.get(link)
        if response.status_code == 200:

    # Don't forget to unzip

'''
Fix column names
'''

'''
----------------------------------------------------------------------------------------------
Main Control Flow
Note that the actual steps run are configured in the YAML input! This allows you
to e.g. skip previously run steps.
'''

def main(args):

    if not confirm_google_vm():
        print('This job needs to run on a Google Cloud Compute Engine to avoid storage egress charges [EXITING]')
        return


    if len(args) != 2:
        print(" ")
        print(" Usage : {} <configuration_yaml>".format(args[0]))
        return

    print('job started')

    #
    # Get the YAML config loaded:
    #

    with open(args[1], mode='r') as yaml_file:
        params, bq_filters, steps = load_config(yaml_file.read())


    #
    # BQ does not like to be given paths that have "~". So make all local paths absolute:
    #

    home = expanduser("~")
    local_files_dir = "{}/{}".format(home, params['LOCAL_FILES_DIR'])
    manifest_file = "{}/{}".format(home, params['MANIFEST_FILE'])
    local_pull_list = "{}/{}".format(home, params['LOCAL_PULL_LIST'])
    file_traversal_list = "{}/{}".format(home, params['FILE_TRAVERSAL_LIST'])
    hold_schema_dict = "{}/{}".format(home, params['HOLD_SCHEMA_DICT'])
    hold_schema_list = "{}/{}".format(home, params['HOLD_SCHEMA_LIST'])

    if 'clear_target_directory' in steps:
        print('clear_target_directory')
        create_clean_target(local_files_dir)

    if 'build_pull_list' in steps:
        print('build_pull_list')
        bucket_location = '{}.{}.{}'

        success =

        if not success:
            print("Build pull list failed")
            return
