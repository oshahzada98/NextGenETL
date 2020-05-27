"""
Copyright 2020, Institute for Systems Biology

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
import math
import sys
import json
import os
import time
# from gdc_clinical_resources.test_data_integrity import *
from common_etl.utils import (
    get_table_prefixes, get_bq_name, has_fatal_error, get_query_results, get_field_name,
    get_tables, get_parent_table, get_parent_field_group, load_config, get_scratch_dir,
    get_cases_by_program, upload_to_bucket, create_and_load_table,
    get_field_depth, get_full_field_name, create_schema_dict, to_bq_schema_obj,
    get_count_field, get_table_case_id_name, get_sorted_table_depths)

API_PARAMS = dict()
BQ_PARAMS = dict()
YAML_HEADERS = ('api_params', 'bq_params', 'steps')


####
#
# Getter functions, employed for readability/consistency
#
##
def generate_long_name(program_name, table):
    """
    Generate string representing a unique name, constructed from elements of
    the table name, program name and GDC release number. Used for storage
    bucket file and BQ table naming.
    :param program_name: Program to which this table is associated.
    :param table: Table name.
    :return: String representing a unique string identifier.
    """
    prefixes = get_table_prefixes(API_PARAMS)
    prefix = prefixes[table]

    # remove invalid char from program name
    if '.' in program_name:
        program_name = '_'.join(program_name.split('.'))

    file_name_parts = [API_PARAMS['GDC_RELEASE'], BQ_PARAMS['TABLE_PREFIX'], program_name]

    # if one-to-many table, append suffix
    if prefix:
        file_name_parts.append(prefix)

    return '_'.join(file_name_parts)


def get_jsonl_filename(program_name, table):
    """
    Gets unique (per release) jsonl filename, used for intermediately storing
    the table rows after they're flattened, but before BQ insertion. Allows for
    faster script thanks to minimal BigQuery txns.
    :param program_name: name of the program to with the data belongs
    :param table: future insertion table for flattened data
    :return: String .jsonl filename, of the form
        relXX_TABLE_NAME_FULL_PROGRAM_supplemental-table-name
        (_supplemental-table-name optional)
    """
    return generate_long_name(program_name, table) + '.jsonl'


def get_temp_filepath(program_name, table):
    """
    Get filepath for the temp storage folder.
    :param program_name: Program
    :param table: Program to which this table is associated.
    :return: String representing the temp file path.
    """
    return get_scratch_dir(API_PARAMS) + '/' + get_jsonl_filename(program_name, table)


def get_full_table_name(program_name, table):
    """
    Get the full name used in table_id for a given table.
    :param program_name: name of the program to with the data belongs
    :param table: Name of desired table
    :return: String representing table name used by BQ.
    """
    return generate_long_name(program_name, table)


def get_required_columns(table):
    """
    Get list of required columns. Currently generated, but intended to also
    work if supplied in YAML config file.
    :param table: name of table for which to retrieve required columns.
    :return: list of required columns (currently, only includes the table's id column)
    """
    table_id_field = get_table_id_key(table)
    table_id_name = get_full_field_name(table, table_id_field)
    return [table_id_name]


def get_table_id_key(table_key):
    """
    Retrieves the id key used to uniquely identify a table record.
    :param table_key: Table for which to determine the id key.
    :return: String representing table key.
    """
    if not API_PARAMS['TABLE_METADATA']:
        has_fatal_error("params['TABLE_METADATA'] not found")

    if 'table_id_key' not in API_PARAMS['TABLE_METADATA'][table_key]:
        has_fatal_error("table_id_key not found in "
                        "API_PARAMS['TABLE_METADATA']['{}']".format(table_key))

    return API_PARAMS['TABLE_METADATA'][table_key]['table_id_key']


def get_id_index(table_key, column_order_dict):
    """
    Get the relative order index of the table's id column.
    :param table_key: Table for which to get index
    :param column_order_dict: Dictionary containing column names : indexes
    :return: Int representing relative column position in schema.
    """
    table_id_key = get_table_id_key(table_key)
    return column_order_dict[table_key + '.' + table_id_key]


def get_count_column_name(table_key):
    """
    Returns name of count column for given one-to-many table.
    :param table_key: one-to-many table
    :return: count column name
    """
    return get_bq_name(API_PARAMS, 'count', table_key)


####
#
# Functions which retrieve preliminary information used for creating table
# schemas / ingesting data
#
##
def get_programs_list():
    """
    Get list of programs represented in GDC API master pull.
    :return: List of GDC programs.
    """
    programs_table_id = (BQ_PARAMS['WORKING_PROJECT'] + '.' +
                         BQ_PARAMS['METADATA_DATASET'] + '.' +
                         API_PARAMS['GDC_RELEASE'] + '_caseData')

    programs = set()
    results = get_query_results("SELECT distinct(program_name) FROM `{}`"
                                .format(programs_table_id))

    for result in results:
        programs.add(result.program_name)

    return programs


def build_column_order_dict():
    """
    Using table order provided in YAML, with add't ordering for reference
    columns added during one-to-many table creation.
    :return: dict of str column names : int representing position.
    """
    column_order_dict = dict()
    field_groups = API_PARAMS['TABLE_ORDER']
    id_index_gap = len(field_groups) - 1

    idx = 0

    for group in field_groups:
        try:
            param_column_order = API_PARAMS['TABLE_METADATA'][group]['column_order']
            id_column = API_PARAMS['TABLE_METADATA'][group]['table_id_key']

            for column in param_column_order:
                column_order_dict[group + '.' + column] = idx
                idx = idx + (id_index_gap * 2) if id_column == column else idx + 1
        except KeyError:
            has_fatal_error("{} found in API_PARAMS['TABLE_ORDER'] but not in "
                            "API_PARAMS['TABLE_METADATA']".format(group))

    column_order_dict['cases.state'] = idx
    column_order_dict['cases.created_datetime'] = idx + 1
    column_order_dict['cases.updated_datetime'] = idx + 2

    return column_order_dict


def get_column_order(table):
    """
    Returns table's column order list (from yaml config file)
    :param table: table for which to retrieve column order
    :return: table's column order list
    """
    if table not in API_PARAMS['TABLE_METADATA']:
        has_fatal_error("'{}' not found in API_PARAMS['TABLE_METADATA']".format(table))
    elif 'column_order' not in API_PARAMS['TABLE_METADATA'][table]:
        has_fatal_error("no column order provided for {} in yaml config.".format(table))

    ordered_table_fields = API_PARAMS['TABLE_METADATA'][table]['column_order']

    master_index_dict = build_column_order_dict()

    table_column_order = [table + '.' + field for field in ordered_table_fields]

    return {col: master_index_dict[col] for col in table_column_order}


####
#
# Functions used to determine a program's table structure(s)
#
##
def get_excluded_fields(table):
    """
    Get list of fields to exclude from final BQ tables (from yaml config file)
    :param table: table key for which to return excluded fields
    :return: list of excluded fields
    """
    if not API_PARAMS['TABLE_METADATA']:
        has_fatal_error("params['TABLE_METADATA'] not found")

    if 'excluded_fields' not in API_PARAMS['TABLE_METADATA'][table]:
        has_fatal_error("excluded_fields not found in API_PARAMS for {}".format(table))

    excluded_fields = API_PARAMS['TABLE_METADATA'][table]['excluded_fields']

    # return [get_full_field_name(table, field) for field in excluded_fields]
    return excluded_fields


def get_all_excluded_columns():
    """
    Get excluded fields for all field groups (from yaml config file)
    :return: list of excluded fields
    """
    excluded_columns = set()

    if not API_PARAMS['TABLE_METADATA']:
        has_fatal_error("params['TABLE_METADATA'] not found")

    if not API_PARAMS['TABLE_ORDER']:
        has_fatal_error("params['TABLE_ORDER'] not found")

    for table in API_PARAMS['TABLE_ORDER']:
        if 'excluded_fields' not in API_PARAMS['TABLE_METADATA'][table]:
            has_fatal_error("{}'s excluded_fields not found in API_PARAMS".format(table))

        excluded_fields = API_PARAMS['TABLE_METADATA'][table]['excluded_fields']

        for field in excluded_fields:
            excluded_columns.add(get_bq_name(API_PARAMS, field, table))

    return excluded_columns


def flatten_tables(field_groups, record_counts):
    """
    From dict containing table_name keys and sets of column names, remove
    excluded columns and merge into parent table if the field group can be
    flattened for this program.
    :param field_groups: dict of tables and columns
    :param record_counts: set of table names
    :return: flattened table column dict.
    """
    record_counts = get_tables(record_counts)
    table_columns = dict()

    fg_depths = {fg: get_field_depth(fg) for fg in field_groups}

    for field_group, depth in sorted(fg_depths.items(), key=lambda i: i[1]):
        if depth > 3:
            has_fatal_error("This script isn't confirmed to work with field groups "
                            "nested more than two levels.")

        field_groups[field_group] = remove_excluded_fields(field_groups[field_group],
                                                           field_group)

        full_field_names = {get_full_field_name(field_group, field)
                            for field in field_groups[field_group]}

        if field_group in record_counts:
            table_columns[field_group] = full_field_names
        else:
            # field group can be flattened
            parent_table = get_parent_table(record_counts, field_group)

            table_columns[parent_table] |= full_field_names

    return table_columns


def examine_case(non_null_fields, record_counts, field_group, fg_name):
    """
    Recursively examines case and updates dicts of non-null fields and max record counts.
    :param non_null_fields: current dict of non-null fields for each field group
    :param field_group: whole or partial case record json object
    :param record_counts: dict of max field group record counts observed in program so far
    :param fg_name: name of currently-traversed field group
    :return: dicts of non-null field lists and max record counts (keys = field groups)
    """
    for field, record in field_group.items():
        if isinstance(record, list):
            child_fg = fg_name + '.' + field

            if child_fg not in record_counts:
                non_null_fields[child_fg] = set()
                record_counts[child_fg] = len(record)
            else:
                record_counts[child_fg] = max(record_counts[child_fg], len(record))

            for entry in record:
                examine_case(non_null_fields, record_counts,
                             field_group=entry, fg_name=child_fg)
        else:
            if fg_name not in non_null_fields:
                non_null_fields[fg_name] = set()
                record_counts[fg_name] = 1

            if isinstance(record, dict):
                for child_field in record:
                    non_null_fields[fg_name].add(child_field)
            else:
                if record:
                    non_null_fields[fg_name].add(field)

    return non_null_fields, record_counts


def find_program_structure(cases):
    """
    Determine table structure required for the given program.
    :param cases: dict of program's case records
    :return: dict of tables and columns, dict with maximum record count for
    this program's field groups.
    """
    non_null_fields = {}
    record_counts = {}

    for case in cases:
        if not case:
            continue
        examine_case(non_null_fields, record_counts, field_group=case, fg_name='cases')

    columns = flatten_tables(non_null_fields, record_counts)

    record_counts = {k: v for k, v in record_counts.items() if record_counts[k] > 0}

    return columns, record_counts


####
#
# Functions used for schema creation
#
##
def get_count_column_index(table_key, column_order_dict):
    """
    Get index of child table record count reference column.
    :param table_key: table for which to get index
    :param column_order_dict: dict containing column indexes
    :return: count column start idx position
    """
    table_id_key = get_table_id_key(table_key)
    id_column_index = column_order_dict[table_key + '.' + table_id_key]

    field_groups = API_PARAMS['TABLE_ORDER']
    id_index_gap = len(field_groups) - 1

    return id_column_index + id_index_gap


def get_case_id_index(table_key, column_orders):
    """
    Get case_id's position index for given table
    :param table_key: table in which to lookup case_id
    :param column_orders: dict of {field names: position indexes}
    :return: case_id index for provided table
    """
    return get_count_column_index(table_key, column_orders) - 1


def generate_id_schema_entry(column, parent_table):
    """
    Create schema entry for inserted parent reference id.
    :param column: parent id column
    :param parent_table: parent table name
    :return: schema entry dict for new reference id field
    """
    parent_fg = get_field_name(parent_table)
    source_table = '*_{}'.format(parent_fg) if parent_table != 'cases' else 'main'
    description = ("Reference to the record's parent id ({}), (located in {} table)."
                   .format(column, source_table))

    return {
        "name": get_field_name(column),
        "type": 'STRING',
        "description": description,
        "mode": 'NULLABLE'
    }


def generate_count_schema_entry(count_id_key, parent_table):
    """
    Create schema entry for one-to-many record count field.
    :param count_id_key: count field name
    :param parent_table: parent table name
    :return: schema entry dict for new one-to-many record count field
    """
    description = ("Total child record count (located in {} table).".format(parent_table))

    return {
        "name": get_field_name(count_id_key),
        "type": 'INTEGER',
        "description": description,
        "mode": 'NULLABLE'
    }


def add_parent_id_to_table(schema, columns, column_order, table, pid_index):
    parent_fg = get_parent_field_group(table)
    ancestor_id_key = parent_fg + '.' + get_table_id_key(parent_fg)

    # add pid to one-to-many table
    schema[ancestor_id_key] = generate_id_schema_entry(ancestor_id_key, parent_fg)
    columns[table].add(ancestor_id_key)
    column_order[table][ancestor_id_key] = pid_index


def add_case_id_to_table(schema, columns, column_order, table, case_id_index):
    # add case_id to one-to-many table
    case_id_name = get_table_case_id_name(table)
    schema[case_id_name] = generate_id_schema_entry(case_id_name, 'main')
    columns[table].add(case_id_name)
    column_order[table][case_id_name] = case_id_index


def add_count_col_to_parent_table(schema, columns, column_order, table):
    # add one-to-many record count column to parent table
    parent_table = get_parent_table(columns.keys(), table)
    count_field = get_count_field(table)

    schema[count_field] = generate_count_schema_entry(count_field, parent_table)
    columns[parent_table].add(count_field)
    count_column_index = get_count_column_index(parent_table, column_order[parent_table])
    column_order[parent_table][count_field] = count_column_index


def add_reference_columns(schema, columns, record_counts):
    """
    Add reference columns generated by separating and flattening data.

    Possible types:

    - _count column representing # of child records found in supplemental table
    - case_id, used to reference main table records
    - pid, used to reference nearest un-flattened ancestor table

    :param record_counts:
    :param columns: dict containing table column keys
    :param schema: dict containing schema records
    :return: table_columns, schema_dict, column_order_dict
    """
    column_orders = dict()

    for table, depth in get_sorted_table_depths(record_counts):
        # get ordering for table by only including relevant column indexes
        column_orders[table] = get_column_order(table)

        if depth == 1 or table not in columns:
            continue

        curr_index = get_id_index(table, column_orders[table]) + 1

        # for formerly doubly-nested tables, ancestor id comes before case_id in schema
        if depth > 2:
            add_parent_id_to_table(schema, columns, column_orders, table, curr_index)
            curr_index += 1

        add_case_id_to_table(schema, columns, column_orders, table, curr_index)

        add_count_col_to_parent_table(schema, columns, column_orders, table)

    return column_orders


def merge_column_orders(schema, columns, record_counts, column_orders):
    # todo delete print
    print("columns: {}".format(columns))

    # todo delete print
    print("record_counts: {}".format(record_counts))

    # todo delete print
    print("column_orders: {}".format(column_orders))

    merged_column_orders = dict()

    for table, depth in get_sorted_table_depths(record_counts, reverse=True):
        if table in columns:
            merged_key = table
        else:
            merged_key = get_parent_table(columns.keys(), table)
            table_id_schema_key = table + "." + get_table_id_key(table)
            # if merging key into parent table, that key is no longer required, might
            # not exist in some cases
            schema[table_id_schema_key]['mode'] = 'NULLABLE'

        if merged_key not in merged_column_orders:
            merged_column_orders[merged_key] = dict()

        merged_column_orders[merged_key].update(column_orders[table])

    # this could probably be optimized, but it doesn't really increase processing time
    for table in get_tables(record_counts):
        for column in merged_column_orders[table].copy():
            if column not in columns[table]:
                merged_column_orders[table].pop(column)

    return merged_column_orders


def create_schemas(columns, record_counts):
    """
    Create ordered schema lists for final tables.
    :param record_counts:
    :param columns: dict containing table column keys
    :return: lists of BQ SchemaFields.
    """
    schema = create_schema_dict(API_PARAMS, BQ_PARAMS)
    # modify schema dict, add reference columns for this program
    column_orders = add_reference_columns(schema, columns, record_counts)

    # reassign merged_column_orders to column_orders
    merged_orders = merge_column_orders(schema, columns, record_counts, column_orders)

    # add bq abbreviations to schema field dicts
    for entry in schema:
        schema[entry]['name'] = get_bq_name(API_PARAMS, entry)

    schema_field_lists = dict()

    for table in get_tables(record_counts):
        # this is just alphabetizing the count columns
        counts_idx = get_count_column_index(table, merged_orders[table])
        count_cols = [col for col, i in merged_orders[table].items() if i == counts_idx]

        for count_column in sorted(count_cols):
            merged_orders[table][count_column] = counts_idx
            counts_idx += 1

        sorted_column_names = [col for col, idx in sorted(merged_orders[table].items(),
                                                          key=lambda i: i[1])]
        schema_field_lists[table] = list()

        for column in sorted_column_names:
            if column in schema:
                schema_field_lists[table].append(to_bq_schema_obj(schema[column]))
            else:
                print("{} not found in src table, excluding schema field.".format(column))

    return schema_field_lists


def remove_excluded_fields(record, table):
    """
    Remove columns with only None values, as well as those excluded.
    :param record: table record to parse.
    :param table: name of destination table.
    :return: Trimmed down record dict.
    """
    excluded_fields = get_excluded_fields(table)

    if isinstance(record, set):
        return {field for field in record if field not in excluded_fields}
    if isinstance(record, dict):
        excluded_fields = {get_bq_name(API_PARAMS, field, table)
                           for field in excluded_fields}
        for field in record.copy():
            if field in excluded_fields or not record[field]:
                record.pop(field)
        return record

    return [field for field in record if field not in excluded_fields]


####
#
# Functions used for parsing and loading data into BQ tables
#
##
def flatten_case_entry(record, field_group, flat_case, case_id, pid, pid_field):
    """
    Recursively traverse the case json object, creating dict of format:
     {field_group: [records]}
    :param record:
    :param field_group:
    :param flat_case: partially-built flattened case dict
    :param case_id: case id
    :param pid: parent field group id
    :param pid_field: parent field group id key
    :return: flattened case dict, format: { 'field_group': [records] }
    """
    # entry represents a field group, recursively flatten each record
    if isinstance(record, list):
        # flatten each record in field group list
        for entry in record:
            flat_case = flatten_case_entry(entry, field_group, flat_case,
                                           case_id, pid, pid_field)
    else:
        row_dict = dict()
        id_field = get_table_id_key(field_group)

        for field, field_val in record.items():
            if isinstance(field_val, list):
                flat_case = flatten_case_entry(
                    record=field_val,
                    field_group=field_group + '.' + field,
                    flat_case=flat_case,
                    case_id=case_id,
                    pid=record[id_field],
                    pid_field=id_field)
            else:
                if id_field != pid_field:
                    parent_fg = get_parent_field_group(field_group)
                    pid_column = get_bq_name(API_PARAMS, pid_field, parent_fg)
                    row_dict[pid_column] = pid

                if id_field != 'case_id':
                    row_dict['case_id'] = case_id
                # Field converted bq column name
                column = get_bq_name(API_PARAMS, field, field_group)
                row_dict[column] = field_val
        if field_group not in flat_case:
            flat_case[field_group] = list()

        excluded_columns = get_all_excluded_columns()

        if row_dict:
            for field in row_dict.copy():
                if field in excluded_columns or not row_dict[field]:
                    row_dict.pop(field)
        flat_case[field_group].append(row_dict)

    return flat_case


def flatten_case(case):
    """
    Converts nested case object into a flattened representation of its records.
    :param case: dict containing case data
    :return: flattened case dict
    """
    return flatten_case_entry(record=case,
                              field_group='cases',
                              flat_case=dict(),
                              case_id=case['case_id'],
                              pid=case['case_id'],
                              pid_field='case_id')


def get_record_idx(flattened_case, field_group, record_id):
    """
    Get index of record associated with record_id from flattened_case
    :param flattened_case: dict containing {field group names: list of record dicts}
    :param field_group: field group containing record_id
    :param record_id: id of record for which to retrieve position
    :return: position index of record in field group's record list
    """
    fg_id_key = get_bq_name(API_PARAMS, get_table_id_key(field_group), field_group)

    idx = 0

    for record in flattened_case[field_group]:
        if record[fg_id_key] == record_id:
            return idx
        idx += 1

    return has_fatal_error("id {} not found by get_record_idx.".format(record_id))


def merge_single_entry_fgs(flattened_case, record_counts):
    """
    # todo
    :param flattened_case:
    :param record_counts:
    :return:
    """
    tables = get_tables(record_counts)

    flattened_fg_parents = dict()

    for field_group in record_counts:
        if field_group == 'cases':
            continue
        if record_counts[field_group] == 1:
            if field_group in flattened_case:
                # create list of flattened field group destination tables
                flattened_fg_parents[field_group] = get_parent_table(tables, field_group)

    for field_group, parent in flattened_fg_parents.items():
        bq_parent_id_key = get_bq_name(API_PARAMS, get_table_id_key(parent), parent)

        for record in flattened_case[field_group]:
            parent_id = record[bq_parent_id_key]
            parent_idx = get_record_idx(flattened_case, parent, parent_id)
            flattened_case[parent][parent_idx].update(record)

        flattened_case.pop(field_group)

    return flattened_case


def get_record_counts(flattened_case, record_counts):
    """
    # todo
    :param flattened_case:
    :param record_counts:
    :return:
    """
    # initialize dict with field groups that can't be flattened
    record_count_dict = {fg: 0 for fg in record_counts if record_counts[fg] > 1}

    for field_group in record_count_dict.copy().keys():
        parent_table = get_parent_table(record_count_dict.keys(), field_group)
        bq_parent_id_key = get_bq_name(API_PARAMS, get_table_id_key(parent_table),
                                       parent_table)

        # initialize record counts for parent id
        if parent_table in flattened_case:
            for parent_record in flattened_case[parent_table]:
                parent_table_id = parent_record[bq_parent_id_key]
                record_count_dict[field_group][parent_table_id] = 0

        # count child records
        if field_group in flattened_case:
            for record in flattened_case[field_group]:
                parent_id = record[bq_parent_id_key]
                record_count_dict[field_group][parent_id] += 1

    # insert record count into flattened dict entries
    for field_group, parent_ids_dict in record_count_dict.items():
        parent_table = get_parent_table(record_counts, field_group)
        count_col_name = get_count_column_name(field_group)

        for parent_id, count in parent_ids_dict.items():
            parent_record_idx = get_record_idx(flattened_case, parent_table, parent_id)
            flattened_case[parent_table][parent_record_idx][count_col_name] = count

    return flattened_case


def merge_or_count_records(flattened_case, record_counts):
    """
    If program field group has max record count of 1, flattens into parent table.
    Otherwise, counts record in one-to-many table and adds count field to parent record
    in flattened_case
    :param flattened_case: flattened dict containing case record's values
    :param record_counts: max counts for program's field group records
    :return: modified version of flattened_case
    """

    flattened_case = merge_single_entry_fgs(flattened_case, record_counts)
    # initialize counts for parent_ids for every possible child table (some child tables
    # won't actually have records, and this initialization adds 0 counts in that case)
    flattened_case = get_record_counts(flattened_case, record_counts)

    return flattened_case


'''
def merge_or_count_records(flattened_case, program_record_counts):
    """
    If program field group has max record count of 1, flattens into parent table.
    Otherwise, counts record in one-to-many table and adds count field to parent record
    in flattened_case
    :param flattened_case: flattened dict containing case record's values
    :param program_record_counts: max counts for program's field group records
    :return: modified version of flattened_case
    """
    tables = get_tables(program_record_counts)
    record_count_dict = dict()
    flattened_fg_parents = dict()

    for field_group in program_record_counts:
        if field_group == 'cases':
            continue
        if program_record_counts[field_group] == 1:
            if field_group in flattened_case:
                # create list of flattened field group destination tables
                flattened_fg_parents[field_group] = get_parent_table(tables, field_group)
        else:
            # initialize record count dicts for one-to-many table records
            # key: parent_id, val: child record count
            record_count_dict[field_group] = dict()

    # merge single entry field groups
    for field_group, parent in flattened_fg_parents.items():
        bq_parent_id_key = get_bq_name(API_PARAMS, get_table_id_key(parent), parent)

        for record in flattened_case[field_group]:
            parent_id = record[bq_parent_id_key]
            parent_idx = get_record_idx(flattened_case, parent, parent_id)
            flattened_case[parent][parent_idx].update(record)

        flattened_case.pop(field_group)

    # initialize counts for parent_ids for every possible child table (some child tables
    # won't actually have records, and this initialization adds 0 counts in that case)
    for field_group in record_count_dict.copy().keys():
        parent = get_parent_table(tables, field_group)
        bq_parent_id_key = get_bq_name(API_PARAMS, get_table_id_key(parent), parent)

        # initialize record counts for parent id
        if parent in flattened_case:
            for record in flattened_case[parent]:
                parent_table_id = record[bq_parent_id_key]
                record_count_dict[field_group][parent_table_id] = 0

        # count child records
        if field_group in flattened_case:
            for record in flattened_case[field_group]:
                parent_id = record[bq_parent_id_key]
                record_count_dict[field_group][parent_id] += 1

    # insert record count into flattened dict entries
    for field_group, parent_ids_dict in record_count_dict.items():
        parent = get_parent_table(tables, field_group)
        count_col_name = get_count_column_name(field_group)

        for parent_id, count in parent_ids_dict.items():
            parent_record_idx = get_record_idx(flattened_case, parent, parent_id)
            flattened_case[parent][parent_record_idx][count_col_name] = count

    return flattened_case
'''


def create_and_load_tables(program_name, cases, schemas, record_counts):
    """
    Create jsonl row files for future insertion, store in GC storage bucket,
    then insert the new table schemas and data.
    :param record_counts:
    :param program_name: program for which to create tables
    :param cases: case records to insert into BQ for program
    :param schemas: dict of schema lists for all of this program's tables
    """
    tables = get_tables(record_counts)
    print("\nInserting case records...")
    for table in tables:
        jsonl_file_path = get_temp_filepath(program_name, table)
        # delete last jsonl scratch file so we don't append to it
        if os.path.exists(jsonl_file_path):
            os.remove(jsonl_file_path)

    for case in cases:
        flattened_case = flatten_case(case)
        flattened_case = merge_or_count_records(flattened_case, record_counts)

        for table in flattened_case.keys():
            if table not in tables:
                has_fatal_error("Table {} not found in table keys".format(table))

            jsonl_fp = get_temp_filepath(program_name, table)

            with open(jsonl_fp, 'a') as jsonl_file:
                for row in flattened_case[table]:
                    json.dump(obj=row, fp=jsonl_file)
                    jsonl_file.write('\n')

    for table in tables:
        jsonl_file = get_jsonl_filename(program_name, table)
        table_id = get_full_table_name(program_name, table)

        upload_to_bucket(BQ_PARAMS, API_PARAMS, jsonl_file)
        create_and_load_table(BQ_PARAMS, jsonl_file, schemas[table], table_id)


####
#
# Functions for creating documentation
#
##
def generate_documentation():
    """
    Generate documentation for tables
    """

    # json_doc_file = API_PARAMS['GDC_RELEASE'] + '_' + BQ_PARAMS['TABLE_PREFIX']
    # json_doc_file += '_json_documentation_dump.json'

    # doc_fp = get_scratch_dir(API_PARAMS) + '/' + json_doc_file

    # with open(doc_fp, 'w') as json_file:
    #     json.dump(documentation_dict, json_file)

    # upload_to_bucket(BQ_PARAMS, API_PARAMS, json_doc_file)


####
#
# Script execution
#
##
def print_final_report(start, steps):
    """
    Outputs a basic report of script's results, including total processing
    time and which steps were specified in YAML.
    :param start: float representing script's start time.
    :param steps: set of steps to be performed (configured in YAML)
    """
    seconds = time.time() - start
    minutes = math.floor(seconds / 60)
    seconds -= minutes * 60

    print("Programs script executed in {} min, {:.0f} sec\n".format(minutes, seconds))
    print("Steps completed: ")

    if 'create_and_load_tables' in steps:
        print('\t - created tables and inserted data')
    if 'validate_data' in steps:
        print('\t - validated data (tests not considered exhaustive)')
    if 'generate_documentation' in steps:
        print('\t - generated documentation')
    print('\n\n')


def main(args):
    """
    Script execution function.
    :param args: command-line arguments
    """
    start = time.time()
    steps = []
    # documentation_dict = dict()

    # Load YAML configuration
    if len(args) != 2:
        has_fatal_error("Usage: {} <configuration_yaml>".format(args[0]), ValueError)

    with open(args[1], mode='r') as yaml_file:
        try:
            global API_PARAMS, BQ_PARAMS
            API_PARAMS, BQ_PARAMS, steps = load_config(yaml_file, YAML_HEADERS)
        except ValueError as err:
            has_fatal_error(str(err), ValueError)

    # programs = get_programs_list()
    programs = ['HCMI']

    for program in programs:
        prog_start = time.time()
        # if 'create_and_load_tables' in steps or 'validate_data' in steps:
        if 'create_and_load_tables' in steps:

            print("Executing script for program {}...".format(program))

            cases = get_cases_by_program(API_PARAMS, BQ_PARAMS, program)

            if not cases:
                continue

            # derive the program's table structure by analyzing its case records
            columns, record_counts = find_program_structure(cases)

            # generate table schemas
            table_schemas = create_schemas(columns, record_counts)

            # create tables, flatten and insert data
            create_and_load_tables(program, cases, table_schemas, record_counts)

            prog_end = time.time() - prog_start
            print("{} processed in {:0.1f} seconds!\n".format(program, prog_end))

            '''
            if 'generate_documentation' in steps:
                
                table_ids = {table: get_table_id(BQ_PARAMS, table) for table in tables}

                # converting to JSON serializable form
                table_column_lists = {t: list(v) for t, v in table_columns.items()}

                documentation_dict[program] = {
                    'table_schemas': str(table_schemas),
                    'table_columns': table_column_lists,
                    'table_ids': table_ids,
                    'table_order_dict': table_order_lists
                }
            '''

    if 'generate_documentation' in steps:
        # documentation_dict['metadata'] = dict()
        # documentation_dict['metadata']['API_PARAMS'] = API_PARAMS
        # documentation_dict['metadata']['BQ_PARAMS'] = BQ_PARAMS
        # documentation_dict['metadata']['schema_dict'] = schema_dict
        generate_documentation()

    if 'validate_data' in steps:
        pass

    print_final_report(start, steps)


if __name__ == '__main__':
    main(sys.argv)
