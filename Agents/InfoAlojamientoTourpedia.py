"""
.. module:: AgentTourpedia
AgentTourpedia
*************
:Description: AgentTourpedia
    Tourpedia, puntos de interes en diferentes ciudades
    Acceso mediante API REST, documentacion en http://tour-pedia.org/api/index.html
    Entries de la API interesantes: getPlaces, getPlaceDetails, getPlacesByArea,
    Acceso mediante SPARQL (cuando funciona), punto de acceso http://tour-pedia.org/sparql,
    ontologias usadas http://tour-pedia.org/about/lod.html
:Authors: bejar
    
:Version: 
:Created on: 27/01/2017 9:34 
"""

from amadeus import Client, ResponseError
from AgentUtil.APIKeys import AMADEUS_KEY, AMADEUS_SECRET

from multiprocessing import Process, Queue
import socket
import logging
import argparse
import requests


from rdflib import Graph, RDF, Namespace, RDFS, Literal
from rdflib.namespace import FOAF
from flask import Flask , request

from AgentUtil.AgentsPorts import PUERTO_INFO_ALOJAMIENTO_TOURPEDIA, PUERTO_DIRECTORIO
from AgentUtil.FlaskServer import shutdown_server
from AgentUtil.Agent import Agent
from AgentUtil.ACL import ACL
from AgentUtil.ACLMessages import build_message, send_message, get_message_properties
from AgentUtil.DSO import DSO
from AgentUtil.Logging import config_logger
from AgentUtil.Util import gethostname

__author__ = 'javier'

TOURPEDIA_END_POINT = 'http://tour-pedia.org/api/'

# Definimos los parametros de la linea de comandos
parser = argparse.ArgumentParser()
parser.add_argument('--open', help="Define si el servidor est abierto al exterior o no", action='store_true',
                    default=False)
parser.add_argument('--verbose', help="Genera un log de la comunicacion del servidor web", action='store_true',
                        default=False)
parser.add_argument('--port', type=int, help="Puerto de comunicacion del agente")
parser.add_argument('--dhost', help="Host del agente de directorio")
parser.add_argument('--dport', type=int, help="Puerto de comunicacion del agente de directorio")

# Logging
logger = config_logger(level=1)

# parsing de los parametros de la linea de comandos
args = parser.parse_args()

# Configuration stuff
if args.port is None:
    port = PUERTO_INFO_ALOJAMIENTO_TOURPEDIA
else:
    port = args.port

if args.open:
    hostname = '0.0.0.0'
    hostaddr = gethostname()
else:
    hostaddr = hostname = socket.gethostname()

if args.dport is None:
    dport = PUERTO_DIRECTORIO
else:
    dport = args.dport

if args.dhost is None:
    dhostname = socket.gethostname()
else:
    dhostname = args.dhost

# Flask stuff
app = Flask(__name__)
if not args.verbose:
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

agn = Namespace("http://www.agentes.org#")

# Contador de mensajes
mss_cnt = 0

InfoAlojamientoTourpedia = Agent('InfoAlojamientoTourpedia',
                       agn.InfoAlojamientoTourpedia,
                       'http://%s:%d/comm' % (hostaddr, port),
                       'http://%s:%d/Stop' % (hostaddr, port))

# Directory agent address
DirectoryAgent = Agent('DirectoryAgent',
                       agn.Directory,
                       'http://%s:%d/Register' % (dhostname, dport),
                       'http://%s:%d/Stop' % (dhostname, dport))

# Global triplestore graph
igraph = Graph()

# Vinculamos todos los espacios de nombre a utilizar

cola1 = Queue()

# Flask stuff
app = Flask(__name__)
if not args.verbose:
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

def register_message():
    """
    Envia un mensaje de registro al servicio de registro
    usando una performativa Request y una accion Register del
    servicio de directorio
    :param gmess:
    :return:
    """

    logger.info('Nos registramos')

    global mss_cnt

    gmess = Graph()

    # Construimos el mensaje de registro
    gmess.bind('foaf', FOAF)
    gmess.bind('dso', DSO)
    reg_obj = agn[InfoAlojamientoTourpedia.name + '-Register']
    gmess.add((reg_obj, RDF.type, DSO.Register))
    gmess.add((reg_obj, DSO.Uri, InfoAlojamientoTourpedia.uri))
    gmess.add((reg_obj, FOAF.name, Literal(InfoAlojamientoTourpedia.name)))
    gmess.add((reg_obj, DSO.Address, Literal(InfoAlojamientoTourpedia.address)))
    gmess.add((reg_obj, DSO.AgentType, DSO.HotelsAgent))

    # Lo metemos en un envoltorio FIPA-ACL y lo enviamos
    gr = send_message(
        build_message(gmess, perf=ACL.request,
                      sender=InfoAlojamientoTourpedia.uri,
                      receiver=DirectoryAgent.uri,
                      content=reg_obj,
                      msgcnt=mss_cnt),
        DirectoryAgent.address)
    mss_cnt += 1

    return gr

@app.route("/")
def hello():
    return "Agente InfoTourpedia en marcha!"


@app.route("/comm")
def comunicacion():
    """
    Entry point de comunicación con el agente.

    Retorna un objeto que representa el resultado de una búsqueda de alojamiento o actividades.

    Asumimos que se reciben siempre acciones correctas, que se refieren a lo que puede hacer el agente, y que las
    acciones se reciben en un mensaje de tipo ACL.request.
    """
    global igraph
    global mss_cnt

    logger.info('Peticion de alojamiento recibida')
    
    # Extraemos el mensaje y creamos un grafo con él
    message = request.args["content"]
    msg_graph = Graph()
    msg_graph.parse(data=message)

    msgdic = get_message_properties(msg_graph)

    # Comprobamos que sea un mensaje FIPA-ACL
    if not msgdic:
        # Si no lo es, respondemos que no hemos entendido el mensaje
        res_graph = build_message(Graph(),
                                  ACL["not-understood"],
                                  sender=InfoAlojamientoTourpedia.uri,
                                  msgcnt=mss_cnt)
    elif msgdic["performative"] != ACL.request:
        # Si la performativa no es de tipo 'request', respondemos que no hemos entendido el mensaje
        res_graph = build_message(Graph(),
                                  ACL["not-understood"],
                                  sender=InfoAlojamientoTourpedia.uri,
                                  msgcnt=mss_cnt)
    else:
        res_graph = infoHoteles(msg_graph, msgdic)

    mss_cnt += 1

    logger.info('Respondemos a la peticion')

    return res_graph.serialize(format="xml")


@app.route("/Stop")
def stop():
    """
    Entrypoint que para el agente

    :return:
    """
    tidyup()
    shutdown_server()
    return "Parando Servidor"


def tidyup():
    """
    Acciones previas a parar el agente

    """
    pass


def agentbehavior1():
    """
    Un comportamiento del agente
    :return:
    """
    # Registramos el agente
    gr = register_message()
    pass

def infoHoteles(gm, msgdic):
    busqueda = agn["ConsultarOpcionesAlojamiento"]

    ciudadDestino = gm.value(subject= busqueda, predicate= agn.ciudadDestino)

    # Obtenemos un atracciones en Bercelona que tengan Museu en el nombre
    gr = Graph()
    try:
        response = requests.get(TOURPEDIA_END_POINT+ 'getPlaces',
                    params={'location': ciudadDestino, 'category': 'accommodation', 'name' : 'Hotel'})

        hoteles = response.json()
        h = hoteles[0]
        hotelID = h['id']
        r = requests.get(h['details']) # usamos la llamada a la API ya codificada en el atributo
        detalles_hotel = r.json()
        hotel_obj = agn[hotelID]
        gr.add((hotel_obj, agn.esUn, agn.Hotel))
        gr.add((hotel_obj, agn.Nombre, Literal(detalles_hotel['name'])))
        gr.add((hotel_obj, agn.Direccion, Literal(h['address'])))
        gr.add((hotel_obj, agn.Precio, Literal('Not available')))

        # Aqui realizariamos lo que pide la accion
        # Por ahora simplemente retornamos un Inform-done
        gr = build_message(gr,
                        ACL['confirm'],
                        sender=InfoAlojamientoTourpedia.uri,
                        msgcnt=mss_cnt,
                        receiver=msgdic['sender'], )
    except:
        logger.info('Location not found on database')
        gr = build_message(gr,
                            ACL['failure'],
                            sender=InfoAlojamientoTourpedia.uri,
                            msgcnt=mss_cnt,
                            receiver=msgdic['sender'], )
    finally:
        return gr

if __name__ == '__main__':
    # Ponemos en marcha los behaviors
    ab1 = Process(target=agentbehavior1)
    ab1.start()

    # Ponemos en marcha el servidor
    app.run(host=hostname, port=port)

    # Esperamos a que acaben los behaviors
    ab1.join()
    logger.info('The End')
