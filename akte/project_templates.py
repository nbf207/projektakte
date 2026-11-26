"""Editable starting points; template changes never rewrite existing projects."""
from copy import deepcopy

TEMPLATES = {
    'auto': {'Hersteller': {'type': 'text', 'value': ''}, 'Modell': {'type': 'text', 'value': ''},
             'Baujahr': {'type': 'number', 'value': '', 'unit': 'Jahr'},
             'Erstzulassung': {'type':'date','value':''}, 'FIN': {'type':'text','value':''},
             'Kennzeichen': {'type':'text','value':''}, 'Motorcode': {'type':'text','value':''},
             'Leistung PS': {'type':'number','value':'','unit':'PS'},
             'Leistung kW': {'type':'number','value':'','unit':'kW'},
             'Hubraum': {'type':'number','value':'','unit':'cm³'},
             'Öl-Spezifikation': {'type':'text','value':''},
             'Öl': {'type': 'text', 'value': ''}, 'Ölmenge': {'type': 'number', 'value': '', 'unit': 'l'},
             'Ölfilter': {'type': 'text', 'value': ''}},
    'aquarium': {'Volumen': {'type': 'number', 'value': '', 'unit': 'l'},
                 'Besatz': {'type': 'multiline', 'value': ''}, 'Filter': {'type': 'text', 'value': ''}},
    'drohne': {'Modell': {'type': 'text', 'value': ''}, 'Seriennummer': {'type': 'text', 'value': ''},
               'Firmware': {'type': 'text', 'value': ''}, 'Akku': {'type': 'text', 'value': ''}},
    'frei': {},
}


def initial_fields(kind):
    return deepcopy(TEMPLATES.get(kind, {}))
