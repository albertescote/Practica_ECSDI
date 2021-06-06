# -*- coding: utf-8 -*-
"""
Agente de información de actividades. Se registra en el directorio de agentes como ello.
"""

from AgentUtil.Coordenadas import COORDENADAS
from multiprocessing import Process, Queue
import logging
import argparse
import amadeus

from flask import Flask, request
from rdflib import Graph, Namespace, Literal
from rdflib.namespace import FOAF, RDF

from AgentUtil.ACL import ACL
from AgentUtil.AgentsPorts import PUERTO_INFO_ACTIVIDADES, PUERTO_DIRECTORIO
from AgentUtil.FlaskServer import shutdown_server
from AgentUtil.ACLMessages import build_message, send_message, get_message_properties
from AgentUtil.Agent import Agent
from AgentUtil.Logging import config_logger
from AgentUtil.DSO import DSO
from AgentUtil.Util import gethostname
import socket

from amadeus import Client, ResponseError
from AgentUtil.APIKeys import AMADEUS_KEY, AMADEUS_SECRET
from AgentUtil.IATACodes import convert_to_IATA
from pprint import PrettyPrinter

# Definimos los parámetros de la linea de comandos
parser = argparse.ArgumentParser()
parser.add_argument("--open", help="Define si el servidor está abierto al exterior o no.", action="store_true",
                    default=False)
parser.add_argument("--port", type=int, help="Puerto de comunicación del agente.")
parser.add_argument("--dhost", help="Host del agente de directorio.")
parser.add_argument("--dport", type=int, help="Puerto de comunicación del agente de directorio.")
parser.add_argument("--verbose", help="Genera un log de la comunicación del servidor web.", action="store_true",
                    default=False)

# Logging
logger = config_logger(level=1)

# Parsing de los parámetros de la línea de comandos
args = parser.parse_args()

# Configuración
if args.open:
    hostname = "0.0.0.0"
    hostaddr = gethostname()
else:
    hostaddr = hostname = socket.gethostname()

if args.port is None:
    port = PUERTO_INFO_ACTIVIDADES
else:
    port = args.port

if args.dhost is None:
    dhostname = socket.gethostname()
else:
    dhostname = args.dhost

if args.dport is None:
    dport = PUERTO_DIRECTORIO
else:
    dport = args.dport

if not args.verbose:
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)

agn = Namespace("http://www.agentes.org#")

# Datos del agente de información de actividades
InfoActividades = Agent("InfoActividades",
                        agn.InfoActividades,
                        "http://%s:%d/comm" % (hostaddr, port),
                        "http://%s:%d/Stop" % (hostaddr, port))

# Datos del agente directorio
DirectoryAgent = Agent("DirectoryAgent",
                       agn.Directory,
                       "http://%s:%d/Register" % (dhostname, dport),
                       "http://%s:%d/Stop" % (dhostname, dport))

# Grafo de estado del agente
igraph = Graph()

# Instanciamos el servidor Flask
app = Flask(__name__)

# Contador de mensajes
mss_cnt = 0


# ENTRY POINTS
@app.route("/comm")
def comunicacion():
    """
    Entry point de comunicación con el agente.

    Retorna un objeto que representa el resultado de una búsqueda de actividades.

    Asumimos que se reciben siempre acciones correctas, que se refieren a lo que puede hacer el agente, y que las
    acciones se reciben en un mensaje de tipo ACL.request.
    """
    global igraph
    global mss_cnt

    logger.info("Petición de información de actividades recibida.")
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
                                  sender=InfoActividades.uri,
                                  msgcnt=mss_cnt)
    elif msgdic["performative"] != ACL.request:
        # Si la performativa no es de tipo 'request', respondemos que no hemos entendido el mensaje
        res_graph = build_message(Graph(),
                                  ACL["not-understood"],
                                  sender=InfoActividades.uri,
                                  msgcnt=mss_cnt)
    else:
        res_graph = infoActividades(msg_graph, msgdic)

    mss_cnt += 1

    logger.info("El agente de información de actividades responde a la petición.")

    return res_graph.serialize(format="xml")


@app.route("/Stop")
def stop():
    """
    Entrada que para el agente.
    """
    tidyup()
    shutdown_server()
    return "Parando Servidor"


def tidyup():
    """
    Acciones previas a parar el agente.
    """
    pass


def infoActividades(msg_graph, msgdic):
    """
    Devuelve un mensaje en formato FIPA-ACL, de tipo 'inform', que contiene el resultado de la búsqueda de actividades
    hecha en la API Amadeus con los criterio de búsqueda que hay en el grafo msg_graph, pasado como parámetro. En caso
    de producirse un error, la función retorna un mensaje FIPA-ACL de tipo 'failure'.
    """
    res_graph = Graph()

    # Extraemos los campos de búsqueda del contenido del mensaje, una vez que este está expresado como un grafo
    search_req = agn["GestorActividades-InfoSearch"]

    ciudadDestino = msg_graph.value(subject=search_req, predicate=agn.ciudadDestino)
    ciudadIATA = convert_to_IATA(str(ciudadDestino))
    radius = msg_graph.value(subject=search_req, predicate=agn.radius)

    amadeus = Client(
        client_id=AMADEUS_KEY,
        client_secret=AMADEUS_SECRET
    )

    try:
        # Hace la búsqueda a la API Amadeus a través de su librería y guarda el resultado en formato JSON (accesible
        # como si fuera un diccionario Python)   
        response = amadeus.shopping.activities.get(latitude=COORDENADAS[str(ciudadIATA)]['latitude'],
                                                   longitude=COORDENADAS[str(ciudadIATA)]['longitude'],
                                                   radius=radius)

        activity = response.data[0]

        activity_obj = agn[activity['id']]
        res_graph.add((activity_obj, agn.esUn, agn.activity))
        res_graph.add((activity_obj, agn.nombre, Literal(activity['name'])))

        res_graph = build_message(res_graph,
                                  ACL['confirm'],
                                  sender=InfoActividades.uri,
                                  msgcnt=mss_cnt,
                                  receiver=msgdic['sender'])
    except ResponseError as error:
        logger.info(error)
        res_graph = build_message(res_graph,
                                  ACL["failure"],
                                  sender=InfoActividades.uri,
                                  receiver=msgdic['sender'],
                                  msgcnt=mss_cnt)
    finally:
        return res_graph


def registrar_actividades():
    """
    Envia un mensaje de registro al servicio de registro usando una performativa 'Request' con
    una acción 'Register' del servicio de directorio.
    """
    global mss_cnt

    logger.info("Registro agente información de actividades.")

    msg_graph = Graph()

    # Vinculamos los espacios de nombres que usaremos para construir el mensaje de registro
    msg_graph.bind("rdf", RDF)
    msg_graph.bind("foaf", FOAF)
    msg_graph.bind("dso", DSO)

    reg_obj = agn[InfoActividades.name + '-Register']
    msg_graph.add((reg_obj, RDF.type, DSO.Register))
    msg_graph.add((reg_obj, DSO.Uri, InfoActividades.uri))
    msg_graph.add((reg_obj, FOAF.name, Literal(InfoActividades.name)))
    msg_graph.add((reg_obj, DSO.Address, Literal(InfoActividades.address)))
    msg_graph.add((reg_obj, DSO.AgentType, DSO.TravelServiceAgent))

    # Lo metemos en un envoltorio FIPA-ACL y lo enviamos
    gr = send_message(
        build_message(msg_graph,
                      perf=ACL.request,
                      sender=InfoActividades.uri,
                      receiver=DirectoryAgent.uri,
                      content=reg_obj,
                      msgcnt=mss_cnt),
        DirectoryAgent.address)
    mss_cnt += 1

    return gr


if __name__ == '__main__':
    try:
        gr = registrar_actividades()
    except:
        logger.info("DirectoryAgent no localizado.")

    # Ponemos en marcha el servidor Flask
    app.run(host=hostname, port=port)
    logger.info('The End')
