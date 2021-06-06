# -*- coding: utf-8 -*-
"""
Agente que busca en el directorio un agente de información de transportes y, una vez obtenida su dirección, le hace
una petición de búsqueda de transporte (con sus respectivas restricciones).
"""

import argparse
import logging
import socket

from flask import Flask, request
from rdflib import Graph, Namespace, Literal
from rdflib.namespace import RDF

from AgentUtil.ACL import ACL
from AgentUtil.ACLMessages import build_message, send_message, get_message_properties
from AgentUtil.Agent import Agent
from AgentUtil.AgentsPorts import PUERTO_GESTOR_TRANSPORTE, PUERTO_DIRECTORIO
from AgentUtil.DSO import DSO
from AgentUtil.FlaskServer import shutdown_server
from AgentUtil.Logging import config_logger
from AgentUtil.Util import gethostname

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
    port = PUERTO_GESTOR_TRANSPORTE
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

# Datos del agente gestor de transporte
GestorTransporte = Agent("GestorTransporte",
                         agn.GestorTransporte,
                         "http://%s:%d/comm" % (hostaddr, port),
                         "http://%s:%d/Stop" % (hostaddr, port))

# Datos del agente directorio
DirectoryAgent = Agent("DirectoryAgent",
                       agn.Directory,
                       "http://%s:%d/Register" % (dhostname, dport),
                       "http://%s:%d/Stop" % (dhostname, dport))

# Grafo de estado del agente
gtgraph = Graph()

# Instanciamos el servidor Flask
app = Flask(__name__)

# Contador de mensajes
mss_cnt = 0


# ENTRY POINTS
@app.route("/comm")
def comunication():
    """
    Entry point de comunicación con el agente.

    Retorna un objeto que representa la selección de un billete de vuelo de entre un conjunto de opciones posibles.
    """
    global gtgraph
    global mss_cnt

    logger.info("Recibe petición de selección de transporte.")

    # Extraemos el mensaje y creamos un grafo con él
    message = request.args["content"]
    req_graph = Graph()
    req_graph.parse(data=message)

    reqdic = get_message_properties(req_graph)

    # Comprobamos que sea un mensaje FIPA-ACL
    if not reqdic:
        # Si no lo es, respondemos que no hemos entendido el mensaje
        res_graph = build_message(Graph(),
                                  ACL["not-understood"],
                                  sender=GestorTransporte.uri,
                                  msgcnt=mss_cnt)
    elif reqdic["performative"] != ACL.request:
        # Si la performativa no es de tipo 'request', respondemos que no hemos entendido el mensaje
        res_graph = build_message(Graph(),
                                  ACL["not-understood"],
                                  sender=GestorTransporte.uri,
                                  msgcnt=mss_cnt)
    else:
        # Busca en el directorio un agente de vuelos
        res_graph = directory_search(DSO.FlightsAgent)

        # Obtiene la dirección del agente en la respuesta
        msg = res_graph.value(predicate=RDF.type, object=ACL.FipaAclMessage)
        content = res_graph.value(subject=msg, predicate=ACL.content)
        agn_addr = res_graph.value(subject=content, predicate=DSO.Address)
        agn_uri = res_graph.value(subject=content, predicate=DSO.Uri)

        # Envía una mensaje de tipo ACL.request al agente de información de vuelos
        res_graph = infoagent_search(agn_addr, agn_uri, req_graph)

        # Selecciona un billete cualquiera del conjunto de billetes recibido, que cumplen con las restricciones
        # de búsqueda. Es una selección simple que no tiene en cuenta otra preferencias del usuario, como su
        # historial de compra pasado.
        gsearch = res_graph.triples((None, agn.esUn, agn.Billete))
        billete = next(gsearch)[0]
        aux_graph = Graph()
        for subject, predicate, object in res_graph.triples((billete, None, None)):
            aux_graph.add((billete, predicate, object))

        res_graph = build_message(aux_graph,
                                  ACL["confirm"],
                                  sender=GestorTransporte.uri,
                                  msgcnt=mss_cnt)

    mss_cnt += 1
    logger.info("Responde a la petición.")

    return res_graph.serialize(format='xml')


@app.route("/Stop")
def stop():
    """
    Entrada que para el agente.
    """
    tidyup()
    shutdown_server()
    return "Parando servidor."


def tidyup():
    """
    Acciones previas a parar el agente.
    """
    pass


def directory_search(agent_type):
    """
    Busca en el servicio de registro un agente del tipo 'agent_type'. Para ello manda un mensaje
    de tipo ACL.request con una acción Search del servicio de directorio.
    """
    global mss_cnt

    logger.info("Busca en el servicio de directorio un agente del tipo 'FlightsAgent'.")

    msg_graph = Graph()

    # Vinculamos los espacios de nombres que usaremos para construir el mensaje de búsqueda
    msg_graph.bind("rdf", RDF)
    msg_graph.bind("dso", DSO)

    # Construimos el mensaje de búsqueda
    obj = agn["GestorTransporte-Search"]
    msg_graph.add((obj, RDF.type, DSO.Search))
    msg_graph.add((obj, DSO.AgentType, agent_type))

    res_graph = send_message(build_message(msg_graph,
                                           ACL.request,
                                           sender=GestorTransporte.uri,
                                           receiver=DirectoryAgent.uri,
                                           content=obj,
                                           msgcnt=mss_cnt),
                             DirectoryAgent.address)

    mss_cnt += 1
    logger.info("Recibe información de un agente del tipo 'FlightsAgent'.")

    return res_graph


def infoagent_search(agn_addr, agn_uri, req_graph):
    """
    Hace una petición de búsqueda al agente de información de transporte (con sus respectivas restricciones) y obtiene
    el resultado. Para ello manda un mensaje de tipo ACL.request con una acción Search del agente de información.
    """
    global mss_cnt

    logger.info("Hacemos una petición al servicio de información de vuelos.")

    # Extraemos del grafo de petición el valor de los campos
    selection_req = agn["AgenteUnificador-SeleccionTransporte"]
    originCity = req_graph.value(subject=selection_req, predicate=agn.originCity)
    destinationCity = req_graph.value(subject=selection_req, predicate=agn.destinationCity)
    departureDate = req_graph.value(subject=selection_req, predicate=agn.departureDate)
    comebackDate = req_graph.value(subject=selection_req, predicate=agn.comebackDate)
    budget = req_graph.value(subject=selection_req, predicate=agn.budget)

    msg_graph = Graph()

    # Supuesta ontología de acciones de agentes de información
    IAA = Namespace('IAActions')

    # Vinculamos los espacios de nombres que usaremos para construir el mensaje de petición
    msg_graph.bind("rdf", RDF)
    msg_graph.bind("iaa", IAA)

    # Construimos el mensaje de petición
    search_req = agn["GestorTransporte-InfoSearch"]
    msg_graph.add((search_req, RDF.type, IAA.SearchFlights))
    msg_graph.add((search_req, agn.originCity, Literal(originCity)))
    msg_graph.add((search_req, agn.destinationCity, Literal(destinationCity)))
    msg_graph.add((search_req, agn.departureDate, Literal(departureDate)))
    msg_graph.add((search_req, agn.comebackDate, Literal(comebackDate)))
    msg_graph.add((search_req, agn.budget, Literal(budget)))

    res_graph = send_message(build_message(msg_graph,
                                           ACL.request,
                                           sender=GestorTransporte.uri,
                                           receiver=agn_uri,
                                           content=search_req,
                                           msgcnt=mss_cnt), agn_addr)

    mss_cnt += 1
    logger.info("Recibe respuesta a la petición al servicio de información de vuelos.")

    return res_graph


if __name__ == "__main__":
    # Ponemos en marcha el servidor Flask
    app.run(host=hostname, port=port)
    logger.info("The end.")
