# -*- coding: utf-8 -*-

def convert_to_IATA(city_name):
    """
    Retorna el código associado a una ciudad según la IATA (International Air Transport Association). De momento,
    solo consideramos las ciudades que aparecen en el diccionario IATA que está en este mismo fichero.
    """
    return IATA[city_name]


IATA = {
    'Barcelona': 'BCN',
    'Paris': 'PAR',
    'Amsterdam': 'AMS',
    'Berlin': 'BER',
    'Dubai': 'DXB',
    'London': 'LHR',
    'Rome': 'FCO'
}
