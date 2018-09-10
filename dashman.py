from os import environ, path

import json
import random
import requests
import string

from datadog import initialize, api
from flask import Flask, render_template, abort, request, jsonify
from flask_bootstrap import Bootstrap

app = Flask(__name__)

TEMP_DIR = 'temp_dashboards'
APP_ROOT = path.dirname(path.abspath(__file__))
UPLOAD_FOLDER = path.join(APP_ROOT, 'static', TEMP_DIR)

# Default values for testing
ENV_API_KEY = environ.get('DD_API_KEY') or ''
ENV_APP_KEY = environ.get('DD_APP_KEY') or ''
DEFAULT_DASH_ID = environ.get('DD_DEFAULT_DASH')

# TODO: Add option to resize widgets
WIDGET_DIMENSIONS = {
    'alert_graph': {'height': 13, 'width': 47},
    'alert_value': {'height': 7, 'width': 15},
    'change': {'height': 13, 'width': 47},
    'check_status': {'height': 9, 'width': 15},
    'event_stream': {'height': 36, 'width': 47},
    'event_timeline': {'height': 9, 'width': 47},
    'free_text': {'height': 9, 'width': 47},
    'hostmap': {'height': 19, 'width': 47},
    'iframe': {'height': 70, 'width': 54},
    'image': {'height': 9, 'width': 15},
    'log_stream': {'height': 38, 'width': 47},
    'manage_status': {'height': 38, 'width': 47},
    'note': {'height': 9, 'width': 47},
    'query_value': {'height': 7, 'width': 15},
    'timeseries': {'height': 13, 'width': 47},
    'toplist': {'height': 13, 'width': 47},
    'trace_service': {'height': 70, 'width': 72}
}


class InvalidUsage(Exception):
    status_code = 500

    def __init__(self, message, status_code=None, payload=None):
        Exception.__init__(self)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.payload = payload

    def to_dict(self):
        rv = dict(self.payload or ())
        rv['message'] = self.message
        return rv


class Dashboard(object):

    def __init__(self, board):
        self.description = board.get('description', '')
        self.template_variables = board.get('template_variables', [])
        self.width = board.get('width', 1024)
        if board.get('widgets', None):
            self.d_type = 'screenboard'
            self.title = board.get('board_title', 'Untitled Screenboard')
            self.charts = board.get('widgets', [])
        elif board.get('graphs', None):
            self.d_type = 'timeboard'
            self.title = board.get('title', 'Untitled Timeboard')
            self.filter_graph_types(board.get('graphs', []))
        else:
            # Dashboard is coming from the UI.  Strip out deselected charts and template vars.
            self.title = board.get('title')
            self.d_type = board.get('type')
            tmp_charts = json.loads(board.get('charts')) or []
            self.charts = [chart for chart in tmp_charts if tmp_charts and chart['selected']]
            tmp_variables = json.loads(board.get('template_variables')) or []
            self.template_variables = [tv for tv in tmp_variables if tmp_variables and tv['selected']]
            vars_to_remove = [tv for tv in tmp_variables if tmp_variables and not tv['selected']]
            self.remove_vars_from_charts(vars_to_remove)
            self.clean_var_json()
            if self.d_type == 'screenboard':
                self.offset_charts()
            self.clean_chart_json()

    def offset_charts(self):
        dash_ids = {chart['dashId'] for chart in self.charts}
        dash_ids = sorted(dash_ids)
        max_x = 0
        for i in range(len(dash_ids)):
            charts = [chart for chart in self.charts if chart['dashId'] == dash_ids[i]]
            for chart in charts:
                chart['x'] = chart['x'] + max_x
            for chart in charts:
                if max_x < chart['x'] + chart['width']:
                    max_x = chart['x'] + chart['width']
            max_x += 1

#    def create_chart_grid(self):
#        # TODO:
#        # option for auto resize
#        # if not auto resize just adjust chart x + max_x
#        # if resize get default values by type
#        # build grid by chart type
#        max_y = 120
#        last_y = 0
#        x_pos = 0
#        last_x = 0
#        max_x = 0
#        for chart in self.charts:
#            y = last_y
#            x = last_x
#            w = chart['width']
#            h = chart['height']
#            if last_y > max_y:
#                y = 0
#                x = max_x + 1
#                last_x = x
#            last_y = y + h + 3
#            if x + w > max_x:
#                max_x = x + w
#            chart['x'] = x
#            chart['y'] = y

    def clean_chart_json(self):
        for chart in self.charts:
            chart.pop('selected', None)
            chart.pop('board_id', None)
            chart.pop('id', None)
            chart.pop('dashId', None)

    def clean_var_json(self):
        for tvar in self.template_variables:
            tvar.pop('selected', None)
            tvar.pop('board_id', None)
            tvar.pop('id', None)
            tvar.pop('dashId', None)

    def remove_vars_from_query(self, qr, vars_to_remove):
        for tvar in vars_to_remove:
            name = tvar.get('name', None)
            only_var_str = '{$' + name + '}'
            begin_str = '{$' + name + ','
            mid_str = ',$' + name + ','
            end_str = ',$' + name + '}'
            qr = qr.replace(only_var_str, '{*}')
            qr = qr.replace(begin_str, '{')
            qr = qr.replace(mid_str, ',')
            qr = qr.replace(end_str, '}')
        return qr

    def remove_vars_from_charts(self, vars_to_remove):
        for chart in self.charts:
            request_list = []
            chart_type = ''
            if self.d_type == 'timeboard':
                if chart.get('definition', None) and chart['definition'].get('requests', None):
                    chart_type = chart['definition'].get('viz', 'timeseries')
                    request_list = chart['definition']['requests']
                    def_key = 'definition'
            elif self.d_type == 'screenboard':
                if chart.get('tile_def', None) and chart['tile_def'].get('requests', None):
                    chart_type = chart['tile_def']['viz']
                    request_list = chart['tile_def']['requests']
                    def_key = 'tile_def'
            if len(request_list) and len(vars_to_remove):
                request_update = []
                for req in request_list:
                    try:
                        if chart_type == 'process':
                            query = req['tag_filters']
                            tf = self.remove_vars_from_query(query, vars_to_remove)
                            req['tag_filters'] = tf
                        else:
                            query = req['q']
                            qr = self.remove_vars_from_query(query, vars_to_remove)
                            req['q'] = qr
                    except KeyError:
                        print("KeyError - Likely due to APM or Log Search")
                        print("Title: {}".format(chart['title']))
                        print("Request: {}".format(req))
                    request_update.append(req)
                chart[def_key]['requests'] = request_update
            elif chart.get('query', None):
                query = chart['query']
                qr = self.remove_vars_from_query(query, vars_to_remove)
                chart['query'] = qr

    def filter_graph_types(self, graphs):
        filtered_graphs = []
        for graph in graphs:
            add_graph = True
            if add_graph:
                filtered_graphs.append(graph)
        self.charts = filtered_graphs

    def convert(self, dest_type):
        dest_type = dest_type.lower()
        if self.d_type == dest_type:
            pass
        elif dest_type == 'timeboard':
            self.d_type = 'timeboard'
            widgets=[]
            not_widgets = ['free_text','alert_value','check_status','event_timeline','event_stream',
                'image','note','alert_graph','iframe']
            tmp = [self.charts[x]['type'] not in not_widgets for x in range(len(self.charts))]
            for x in range(len(tmp)):
                if tmp[x]:
                    widgets.append(self.charts[x])
            self.convert_widgets_to_graphs(widgets)
        elif dest_type == 'screenboard':
            self.d_type = 'screenboard'
            graphs = [g for g in self.charts]
            self.convert_graphs_to_widgets(graphs)

    def convert_widgets_to_graphs(self, widgets):
        graphs = []
        for i in range(len(widgets)):
            if 'tile_def' in widgets[i]:
                if 'conditional_formats' not in widgets[i]['tile_def']['requests'][0]:
                    widgets[i]['tile_def']['requests'][0]['conditional_formats'] = []
            else:
                widgets[i]['tile_def'] = 'outdated'
            if not (('title_text' in widgets[i]) and (isinstance(widgets[i]['title_text'],str))):
                try:
                    widgets[i]['title_text'] = widgets[i]['tile_def']['requests'][0]['q']
                except TypeError:
                    widgets[i]['title_text'] = 'No title'
            if widgets[i]['type'] == 'hostmap':
                definition = {
                    "style": widgets[i]['tile_def']['style'],
                    "requests": widgets[i]['tile_def']['requests'],
                    "viz": widgets[i]['type'],
                    }
                for opt in ['group', 'groupBy', 'noGroupHosts', 'noMetricHosts']:
                    if widgets[i]['tile_def'].get(opt, None) is not None:
                        definition[opt] = widgets[i]['tile_def'][opt]
                graphs.append({
                    "definition": definition,
                    "title": widgets[i]['title_text'],
                })
            elif widgets[i]['tile_def'] == 'outdated':
                    pass
            else:
                graphs.append({
                    "definition":{
                    "events": [],
                    "requests":widgets[i]['tile_def']['requests'],
                    "viz":widgets[i]['type'],
                    },
                    "title": widgets[i]['title_text']
                })
        self.charts = graphs

    def convert_graphs_to_widgets(self, graphs):
        pos_x = 0
        pos_y = 0
        height = 13
        width =  47
        margin = 5
        tmp_y = 0
        widgets = []
        for i in range(len(graphs)):
            if i % 2 == 0 and i != 0:
                pos_x = 0
                tmp_y = pos_y
            elif i % 2 == 1 and i != 0:
                tmp_y = pos_y
                pos_y = pos_y + height + margin
                pos_x = width + margin
            if 'viz' not in graphs[i]['definition']:
                graphs[i]['definition']['viz'] = "timeseries"
            if graphs[i]['definition']['viz'] not in ["hostmap","distribution","heatmap"]:
                widgets.append({
                    'height': height,
                    'width': width,
                    'timeframe': '4h',
                    'x' : pos_x,
                    'y' : tmp_y,
                    "tile_def":{
                    "requests":graphs[i]['definition']['requests'],
                    "viz":graphs[i]['definition']['viz'],
                    },
                    "title_text": graphs[i]['title'],
                    "title": True,
                    "type":graphs[i]['definition']['viz']
                })
            elif graphs[i]['definition']['viz'] == "heatmap" or graphs[i]['definition']['viz'] == "distribution":
                graphs[i]['definition']['requests'][0]['type'] = 'line'
                graphs[i]['definition']['requests'][0]['aggregator'] = 'avg'
                widgets.append({
                    'height': height,
                    'width': width,
                    'timeframe': '4h',
                    'x' : pos_x,
                    'y' : tmp_y,
                    "tile_def":{
                    "requests":graphs[i]['definition']['requests'],
                    "viz":graphs[i]['definition']['viz'],
                    },
                    "title_text": graphs[i]['title'],
                    "title": True,
                    "type":"timeseries"
                })
            elif graphs[i]['definition']['viz'] == "hostmap":
                widgets.append({
                    'height': height,
                    'width': width,
                    'timeframe': '4h',
                    'x' : pos_x,
                    'y' : tmp_y,
                    "tile_def":graphs[i]['definition'],
                    "title_text": graphs[i]['title'],
                    "title": True,
                    "type":"hostmap"
                })
        self.charts = widgets

    def return_file_json(self):
        file_json = {
            'description': self.description,
            'width': self.width,
            'template_variables': self.template_variables
        }
        if self.d_type == 'timeboard':
            file_json['title'] = self.title
            file_json['graphs'] = self.charts
        elif self.d_type == 'screenboard':
            file_json['widgets'] = self.charts
            file_json['board_title'] = self.title
            file_json['width'] = self.width
        return file_json

    def return_ui_json(self):
        ui_json = {
            'type': self.d_type,
            'title': self.title,
            'description': self.description,
            'charts': self.charts,
            'width': self.width,
            'template_variables': self.template_variables,
        }
        return ui_json

def _create_dash(api_key, app_key, dash_dict):

    options = {
        'api_key': api_key,
        'app_key': app_key
    }

    initialize(**options)

    dash_type = dash_dict.get('type', None)
    read_only = False
    res = {'errors': 'Dash creation failed before calling the API.'}
    if dash_type.lower() == 'timeboard':
        title = dash_dict.get('title', 'New Timeboard')
        description = dash_dict.get('description', '')
        graphs = dash_dict.get('charts', [])
        print(graphs) #XYZ
        template_variables = dash_dict.get('template_variables', [])
        res = api.Timeboard.create(title=title, description=description, graphs=graphs,
                                   template_variables=template_variables, read_only=read_only)
    elif dash_type.lower() == 'screenboard':
        board_title = dash_dict.get('title', 'New Screenboard')
        description = dash_dict.get('description', '')
        width = dash_dict.get('width', 1024)
        widgets = dash_dict.get('charts', [])
        template_variables = dash_dict.get('template_variables', [])
        res = api.Screenboard.create(board_title=board_title, description=description,
                                     widgets=widgets, template_variables=template_variables, width=width)
    else:
        pass
    return res

def _get_dash_from_api(api_key, app_key, dash_id):

    options = {
        'api_key': api_key,
        'app_key': app_key
    }

    initialize(**options)

    board = ''

    try:
        res = api.Timeboard.get(dash_id)
        board = res['dash']
    except:
        pass
    if 'errors' in board or not board:
        try:
            board = api.Screenboard.get(dash_id)
        except:
            pass
    return board

def _gen_rand_str():
    return ''.join([random.choice(string.ascii_letters) for n in range(8)])

# Ajax Routes
#TODO: One route for getting the dashboard
@app.route("/load_file", methods=['POST'])
def get_dash_from_file():
    if request.method == 'POST':
        try:
            json_file = request.files['file']
        except KeyError:
            return jsonify({'error': 'No File Submitted'})
        json_file.seek(0)
        dash_json = json.loads(json_file.read())
        dashboard = Dashboard(dash_json)
        ui_json = dashboard.return_ui_json()
        return jsonify(ui_json)
    return None

@app.route("/get_dash", methods=['POST'])
def get_dash_from_api():
    if request.method == 'POST':
        api_key = request.form.get('apiKey')
        app_key = request.form.get('appKey')
        dash_id = int(request.form.get('dashId'))
        dash_json = _get_dash_from_api(api_key, app_key, dash_id)
        if 'errors' in dash_json:
            raise InvalidUsage('Invalid dashboard.  If you are using an integration preset dashboard, please clone the dashboard and use the new id.', status_code=500)
        dashboard = Dashboard(dash_json)
        ui_json = dashboard.return_ui_json()
        return jsonify(ui_json)
    return None

@app.route("/send_dash", methods=['POST'])
def send_dash_to_org():
    if request.method == 'POST':
        api_key = request.form.get('api_key')
        app_key = request.form.get('app_key')
        dest_type = request.form.get('dest_type')

        dashboard = Dashboard(request.form)
        dashboard.convert(dest_type)
        dash_json = dashboard.return_ui_json()

        res = _create_dash(api_key, app_key, dash_json)
        return jsonify(res)
    else:
        return None

@app.route("/save_dash", methods=['POST'])
def save_dash_to_file():
    if request.method == 'POST':
        dest_type = request.form.get('dest_type')

        dashboard = Dashboard(request.form)
        dashboard.convert(dest_type)
        dash_json = dashboard.return_file_json()

        file_name = 'temp_' + _gen_rand_str() + '.json'
        file_path = UPLOAD_FOLDER + '/' + file_name

        with open(file_path, 'w') as f:
            json.dump(dash_json, f, sort_keys=True, indent=4, separators=(',', ': '))

        temp_path = TEMP_DIR + '/' + file_name
        return request.url_root + 'static/' + temp_path
    else:
        return None

@app.errorhandler(InvalidUsage)
def handle_invalid_usage(error):
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response

# User Routes
@app.route('/', methods=['GET', 'POST'])
def dashman():
    if request.method == 'POST':
        return jsonify(data)
    return render_template('dashman.html',
                           api_key=ENV_API_KEY,
                           app_key=ENV_APP_KEY,
                           dash_id=DEFAULT_DASH_ID)

if __name__ == "__main__":
    app.run(host='0.0.0.0', debug=True)
