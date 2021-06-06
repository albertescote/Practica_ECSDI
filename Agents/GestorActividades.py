# -*- coding: utf-8 -*-
"""
Created on Fri Dec 27 15:58:13 2013

Esqueleto de agente usando los servicios web de Flask

/comm es la entrada para la recepcion de mensajes del agente
/Stop es la entrada que para el agente

Tiene una funcion AgentBehavior1 que se lanza como un thread concurrente

Asume que el agente de registro esta en el puerto 9000

@author: javier
"""

from AgentUtil.OntoNamespaces import GR
from multiprocessing import Process, Queue
import socket
import logging
import argparse


from rdflib import Graph, RDF, Namespace, RDFS, Literal
from rdflib.namespace import FOAF
from flask import Flask , request, render_template

from AgentUtil.AgentsPorts import PUERTO_GESTOR_ACTIVIDADES, PUERTO_DIRECTORIO
from AgentUtil.FlaskServer import shutdown_server
from AgentUtil.Agent import Agent
from AgentUtil.ACL import ACL
from AgentUtil.ACLMessages import build_message, send_message, get_message_properties
from AgentUtil.DSO import DSO
from AgentUtil.Logging import config_logger
from AgentUtil.Util import gethostname

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
    port = PUERTO_GESTOR_ACTIVIDADES
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

GestorActividades = Agent('GestorActividades',
                       agn.GestorActividades,
                       'http://%s:%d/comm' % (hostaddr, port),
                       'http://%s:%d/Stop' % (hostaddr, port))

# Datos del agente directorio
DirectoryAgent = Agent("DirectoryAgent",
                       agn.Directory,
                       "http://%s:%d/Register" % (dhostname, dport),
                       "http://%s:%d/Stop" % (dhostname, dport))

# Global triplestore graph
gagraph = Graph()

# Vinculamos todos los espacios de nombre a utilizar
gagraph.bind('acl', ACL)
gagraph.bind('rdf', RDF)
gagraph.bind('rdfs', RDFS)
gagraph.bind('foaf', FOAF)
gagraph.bind('dso', DSO)

cola1 = Queue()

# Flask stuff
app = Flask(__name__)
if not args.verbose:
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

@app.route("/")
def hello():
    return "Agente alojamiento en marcha!"


@app.route("/comm")
def comunicacion():
    """
    Entrypoint de comunicacion
    """
    global gagraph
    global mss_cnt

    logger.info('Peticion de alojamiento recibida')

    # Extraemos el mensaje y creamos un grafo con el
    message = request.args['content']
    req_graph = Graph()
    req_graph.parse(data=message)

    reqdic = get_message_properties(req_graph)

    # Comprobamos que sea un mensaje FIPA ACL
    if reqdic is None:
        # Si no es, respondemos que no hemos entendido el mensaje
        res_graph = build_message(Graph(),
                           ACL['not-understood'], 
                           sender=GestorActividades.uri, 
                           msgcnt=mss_cnt)
    elif reqdic["performative"] != ACL.request:
        # Si la performativa no es de tipo 'request', respondemos que no hemos entendido el mensaje
        res_graph = build_message(Graph(),
                                  ACL["not-understood"],
                                  sender=GestorActividades.uri,
                                  msgcnt=mss_cnt)
    else:
        # Busca en el directorio un agente de vuelos
        res_graph = directory_search(DSO.TravelServiceAgent)

        # Obtiene la dirección del agente en la respuesta
        msg = res_graph.value(predicate=RDF.type, object=ACL.FipaAclMessage)
        content = res_graph.value(subject=msg, predicate=ACL.content)
        agn_addr = res_graph.value(subject=content, predicate=DSO.Address)
        agn_uri = res_graph.value(subject=content, predicate=DSO.Uri)

        # Envía una mensaje de tipo ACL.request al agente de información de vuelos
        res_graph = infoagent_search(agn_addr, agn_uri, req_graph)

        res_graph = build_message(res_graph,
                                  ACL["confirm"],
                                  sender=GestorActividades.uri,
                                  msgcnt=mss_cnt)
            
    mss_cnt += 1

    logger.info('Respondemos a la peticion')

    return res_graph.serialize(format='xml')


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

def directory_search(agent_type):
    """
    Busca en el servicio de registro mandando un
    mensaje de request con una accion Search del servicio de directorio
    Podria ser mas adecuado mandar un query-ref y una descripcion de registo
    con variables
    :param type:
    :return:
    """
    global mss_cnt
    logger.info("Busca en el servicio de directorio un agente del tipo 'TravelServiceAgent'.")

    msg_graph = Graph()

    # Vinculamos los espacios de nombres que usaremos para construir el mensaje de búsqueda
    msg_graph.bind("rdf", RDF)
    msg_graph.bind("dso", DSO)

    obj = agn["GestorActividades-Search"]
    msg_graph.add((obj, RDF.type, DSO.Search))
    msg_graph.add((obj, DSO.AgentType, agent_type))

    msg = build_message(msg_graph, perf=ACL.request,
                        sender=GestorActividades.uri,
                        receiver=DirectoryAgent.uri,
                        content=obj,
                        msgcnt=mss_cnt)
    res_graph = send_message(msg, DirectoryAgent.address)
    mss_cnt += 1
    logger.info('Recibimos informacion del agente')

    return res_graph


def infoagent_search(agn_addr, agn_uri, req_graph):
    """
    Hace una petición de búsqueda al agente de información de actividades (con sus respectivas restricciones) y obtiene
    el resultado. Para ello manda un mensaje de tipo ACL.request con una acción Search del agente de información.
    """
    global mss_cnt

    logger.info("Hacemos una petición al servicio de información de actividades.")

    # Extramos del grafo de petición el valor de los campos 
    selection_req = agn["AgenteUnificador-SeleccionActividades"]
    ciudadDestino = req_graph.value(subject= selection_req, predicate= agn.ciudadDestino)
    radius = req_graph.value(subject= selection_req, predicate= agn.radius)
    diasDeViaje = req_graph.value(subject= selection_req, predicate= agn.diasDeViaje)

    msg_graph = Graph()

    # Supuesta ontología de acciones de agentes de información
    IAA = Namespace('IAActions')

    # Vinculamos los espacios de nombres que usaremos para construir el mensaje de petición
    msg_graph.bind("rdf", RDF)
    msg_graph.bind("iaa", IAA)

    search_req = agn["GestorActividades-InfoSearch"]
    msg_graph.add((search_req, RDF.type, IAA.TravelServiceAgent))
    msg_graph.add((search_req, agn.ciudadDestino, Literal(ciudadDestino)))
    msg_graph.add((search_req, agn.radius, Literal(radius)))

    msg = build_message(msg_graph,
                        perf=ACL.request,
                        sender=GestorActividades.uri,
                        receiver=agn_uri,
                        content=search_req,
                        msgcnt=mss_cnt)
    
    res_graph = send_message(msg, agn_addr)

    selected_grapth = Graph()
    gsearch = res_graph.triples((None, agn.esUn, agn.activity))
    for i in range(int(diasDeViaje)):
        # Seleccionamos actividad de mañana
        actividad = next(gsearch)[0]
        nombre_act = res_graph.value(subject=actividad, predicate=agn.nombre)
        id_act = res_graph.value(subject=actividad, predicate=agn.id)

        activity_obj = agn[id_act]
        selected_grapth.add((activity_obj, agn.esUn, agn.activity))
        selected_grapth.add((activity_obj, agn.nombre, Literal(nombre_act)))
        selected_grapth.add((activity_obj, agn.horario, Literal('mañana')))

        # Seleccionamos actividad de tarde
        actividad = next(gsearch)[0]
        nombre_act = res_graph.value(subject=actividad, predicate=agn.nombre)
        id_act = res_graph.value(subject=actividad, predicate=agn.id)

        activity_obj = agn[id_act]
        selected_grapth.add((activity_obj, agn.esUn, agn.activity))
        selected_grapth.add((activity_obj, agn.nombre, Literal(nombre_act)))
        selected_grapth.add((activity_obj, agn.horario, Literal('tarde')))
        
        # Seleccionamos actividad de noche
        actividad = next(gsearch)[0]
        nombre_act = res_graph.value(subject=actividad, predicate=agn.nombre)
        id_act = res_graph.value(subject=actividad, predicate=agn.id)

        activity_obj = agn[id_act]
        selected_grapth.add((activity_obj, agn.esUn, agn.activity))
        selected_grapth.add((activity_obj, agn.nombre, Literal(nombre_act)))
        selected_grapth.add((activity_obj, agn.horario, Literal('noche')))

    mss_cnt += 1
    logger.info('Actividades recibidas')

    return selected_grapth

if __name__ == '__main__':
    # Ponemos en marcha los behaviors
    #ab1 = Process(target=agentbehavior1)
    #ab1.start()

    # Ponemos en marcha el servidor
    app.run(host=hostname, port=port)

    # Esperamos a que acaben los behaviors
    #ab1.join()
    logger.info('The End')
