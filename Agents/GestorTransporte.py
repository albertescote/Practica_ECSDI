# -*- coding: utf-8 -*-
"""
Agente que busca en el directorio un agente de información de transportes y, una vez obtenida su dirección, le hace
una petición de búsqueda de transporte (con sus respectivas restricciones).
"""

from multiprocessing import Process, Queue
import logging
import argparse

from flask import Flask, render_template, request
from rdflib import Graph, Namespace, Literal
from rdflib.namespace import FOAF, RDF

from AgentUtil.ACL import ACL
from AgentUtil.DSO import DSO
from AgentUtil.FlaskServer import shutdown_server
from AgentUtil.ACLMessages import build_message, send_message
from AgentUtil.Agent import Agent
from AgentUtil.Logging import config_logger
from AgentUtil.Util import gethostname
import socket

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
    port = 9002
else:
    port = args.port

if args.dhost is None:
    dhostname = socket.gethostname()
else:
    dhostname = args.dhost

if args.dport is None:
    dport = 9000
else:
    dport = args.dport

if not args.verbose:
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)

agn = Namespace("http://www.agentes.org#")

# Datos del agente gestor de transporte
GestorTransporte = Agent("GestorTransporte",
                         agn.GestorTransporte,
                         "http://%s:%d/Comm" % (hostaddr, port),
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

# Cola de comunicación entre procesos
queue1 = Queue()


# ENTRY POINTS
@app.route("/Comm")
def comunication():
    """
    Entry point de comunicación con el agente.
    """
    pass


@app.route("/Iface", methods=['GET', 'POST'])
def browser_iface():
    if request.method == 'GET':
        return render_template('iface.html')
    else:
        user = request.form['username']
        mess = request.form['message']
        return render_template('riface.html', user=user, mess=mess)


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
    global queue1
    queue1.put(0)


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


def infoagent_search(agn_addr, agn_uri):
    """
    Hace una petición de búsqueda al agente de información de transporte (con sus respectivas restricciones) y obtiene
    el resultado. Para ello manda un mensaje de tipo ACL.request con una acción Search del agente de información.
    """
    global mss_cnt

    logger.info("Hacemos una petición al servicio de información de vuelos.")

    msg_graph = Graph()

    # Supuesta ontología de acciones de agentes de información
    IAA = Namespace('IAActions')

    # Vinculamos los espacios de nombres que usaremos para construir el mensaje de petición
    msg_graph.bind("rdf", RDF)
    msg_graph.bind("iaa", IAA)

    # Construimos el mensaje de petición
    search_req = agn["GestorTransporte-InfoSearch"]
    msg_graph.add((search_req, RDF.type, IAA.SearchFlights))
    msg_graph.add((search_req, agn.originCity, Literal("Paris")))
    msg_graph.add((search_req, agn.destinationCity, Literal("Barcelona")))
    msg_graph.add((search_req, agn.departureDate, Literal("2021-06-21")))
    msg_graph.add((search_req, agn.budget, Literal("250")))

    res_graph = send_message(build_message(msg_graph,
                                           ACL.request,
                                           sender=GestorTransporte.uri,
                                           receiver=agn_uri,
                                           content=search_req,
                                           msgcnt=mss_cnt), agn_addr)

    mss_cnt += 1
    logger.info("Recibe respuesta a la petición al servicio de información de vuelos.")

    return res_graph


def agentbehaviour1(queue):
    """
    Esta función se ejecuta en paralelo al servidor Flask. Busca en el directorio un agente de información de
    transporte, le hace una petición de búsqueda y obtiene el resultado.
    """
    # Busca en el directorio un agente de vuelos
    res_graph = directory_search(DSO.FlightsAgent)

    # Obtiene la dirección del agente en la respuesta
    msg = res_graph.value(predicate=RDF.type, object=ACL.FipaAclMessage)
    content = res_graph.value(subject=msg, predicate=ACL.content)
    agn_addr = res_graph.value(subject=content, predicate=DSO.Address)
    agn_uri = res_graph.value(subject=content, predicate=DSO.Uri)

    # Envía una mensaje de tipo ACL.request con una acción de tipo Search, que está en una supuesta
    # ontología de acciones de agentes
    res_graph = infoagent_search(agn_addr, agn_uri)

    fin = False
    while not fin:
        while queue.empty():
            pass
        v = queue.get()
        if v == 0:
            print(v)
            return 0
        else:
            print(v)


if __name__ == "__main__":
    # Ponemos en marcha los behaviours como procesos
    ab1 = Process(target=agentbehaviour1, args=(queue1,))
    ab1.start()

    # Ponemos en marcha el servidor Flask
    app.run(host=hostname, port=port)

    # Espera hasta que el proceso hijo termine
    ab1.join()
    logger.info("The end.")
