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

from Agents.InfoAmadeus import InfoAmadeus
from Agents.AgenteUnificador import AgenteAlojamiento
from multiprocessing import Process, Queue
import socket
import logging
import argparse


from rdflib import Graph, RDF, Namespace, RDFS, Literal
from rdflib.namespace import FOAF
from flask import Flask , request, render_template  

from AgentUtil.FlaskServer import shutdown_server
from AgentUtil.Agent import Agent
from AgentUtil.ACL import ACL
from AgentUtil.ACLMessages import build_message, send_message, get_message_properties
from AgentUtil.DSO import DSO
from AgentUtil.Logging import config_logger
from AgentUtil.Util import gethostname

__author__ = 'javier'

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
    port = 9002
else:
    port = args.port

if args.open:
    hostname = '0.0.0.0'
    hostaddr = gethostname()
else:
    hostaddr = hostname = socket.gethostname()

print('DS Hostname =', hostaddr)

if args.dport is None:
    dport = 9000
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

agn = Namespace("http://www.agentes.org/")
myns_pet = Namespace("http://www.agentes.org/peticiones/")
myns_atr = Namespace("http://www.agentes.org/atributos/")
myns_par = Namespace("http://my.namespace.org/parametros/")

# Contador de mensajes
mss_cnt = 0

AgenteAlojamiento = Agent('AgenteAlojamiento',
                       agn.AgenteAlojamiento,
                       'http://%s:%d/comm' % (hostaddr, port),
                       'http://%s:%d/Stop' % (hostaddr, port))

# Directory agent address
DirectoryAgentHotels = Agent('DirectoryAgentHotels',
                       agn.DirectoryAgentHotels,
                       'http://%s:%d/Register' % (dhostname, dport),
                       'http://%s:%d/Stop' % (dhostname, dport))

# Global triplestore graph
dsgraph = Graph()

# Vinculamos todos los espacios de nombre a utilizar
dsgraph.bind('acl', ACL)
dsgraph.bind('rdf', RDF)
dsgraph.bind('rdfs', RDFS)
dsgraph.bind('foaf', FOAF)
dsgraph.bind('dso', DSO)

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
    global dsgraph
    global mss_cnt

    logger.info('Peticion de alojamiento recibida')

    # Extraemos el mensaje y creamos un grafo con el
    message = request.args['content']
    gm = Graph()
    gm.parse(data=message)

    msgdic = get_message_properties(gm)

    # Comprobamos que sea un mensaje FIPA ACL
    if msgdic is None:
        # Si no es, respondemos que no hemos entendido el mensaje
        gr = build_message(Graph(), ACL['not-understood'], sender=AgenteAlojamiento.uri, msgcnt=mss_cnt)
    else:
        # Obtenemos la performativa
        perf = msgdic['performative']

        if perf != ACL.request:
            # Si no es un request, respondemos que no hemos entendido el mensaje
            gr = build_message(Graph(), ACL['not-understood'], sender=AgenteAlojamiento.uri, msgcnt=mss_cnt)
        else:
            # Extraemos el objeto del contenido que ha de ser una accion de la ontologia de acciones del agente
            # de registro

            # Averiguamos el tipo de la accion
            if 'content' in msgdic:
                content = msgdic['content']
                accion = gm.value(subject=content, predicate=RDF.type)

            if accion == DSO.SolverAgent:
                # Buscamos en el directorio
                # un agente de hoteles
                gmess = directory_search_message(DSO.HotelsAgent)

                # Obtenemos la direccion del agente de la respuesta
                # No hacemos ninguna comprobacion sobre si es un mensaje valido
                msg = gmess.value(predicate=RDF.type, object=ACL.FipaAclMessage)
                content = gmess.value(subject=msg, predicate=ACL.content)
                ragn_addr = gmess.value(subject=content, predicate=DSO.Address)
                ragn_uri = gmess.value(subject=content, predicate=DSO.Uri)

                gr = resolverPlan(ragn_addr, ragn_uri, gm)
            else:
                gr = build_message(Graph(),
                                   ACL['not-understood'],
                                   sender=AgenteAlojamiento.uri,
                                   msgcnt=mss_cnt)
            
            
    mss_cnt += 1

    logger.info('Respondemos a la peticion')

    return gr.serialize(format='xml')


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

def directory_search_message(type):
    """
    Busca en el servicio de registro mandando un
    mensaje de request con una accion Search del servicio de directorio
    Podria ser mas adecuado mandar un query-ref y una descripcion de registo
    con variables
    :param type:
    :return:
    """
    global mss_cnt
    logger.info('Buscamos en el servicio de registro')

    gmess = Graph()

    gmess.bind('foaf', FOAF)
    gmess.bind('dso', DSO)
    reg_obj = agn[AgenteAlojamiento.name + '-search']
    gmess.add((reg_obj, RDF.type, DSO.Search))
    gmess.add((reg_obj, DSO.AgentType, type))

    msg = build_message(gmess, perf=ACL.request,
                        sender=AgenteAlojamiento.uri,
                        receiver=DirectoryAgentHotels.uri,
                        content=reg_obj,
                        msgcnt=mss_cnt)
    gr = send_message(msg, DirectoryAgentHotels.address)
    mss_cnt += 1
    logger.info('Recibimos informacion del agente')

    return gr


def agentbehavior1(cola):
    """
    Un comportamiento del agente
    :return:
    """

def resolverPlan(addr, ragn_uri, gm):
    peticion = myns_pet["SolicitarSelecciónAlojamiento"]

    ciudadIATA_destino = gm.value(subject= peticion, predicate= myns_atr.ciudadIATA_destino)
    ciudadDestino = gm.value(subject= peticion, predicate= myns_atr.ciudadDestino)
    dataIda = gm.value(subject= peticion, predicate= myns_atr.dataIda)
    dataVuelta = gm.value(subject= peticion, predicate= myns_atr.dataVuelta)
    precioHotel = gm.value(subject= peticion, predicate= myns_atr.precioHotel)
    estrellas = gm.value(subject= peticion, predicate= myns_atr.estrellas)
    roomQuantity = gm.value(subject= peticion, predicate= myns_atr.roomQuantity)
    adults = gm.value(subject= peticion, predicate= myns_atr.adults)
    radius = gm.value(subject= peticion, predicate= myns_atr.radius)

    gres = getInfoHotels(addr, ragn_uri, ciudadDestino, ciudadIATA_destino, dataIda, dataVuelta, precioHotel, estrellas, roomQuantity, adults, radius)
    msgdic = get_message_properties(gres)
    perf = msgdic['performative'] 
    gr = build_message(gres,
                        perf,
                        sender=AgenteAlojamiento.uri,
                        msgcnt=mss_cnt,
                        receiver=ragn_uri, 
                    )
    return gr

def getInfoHotels(addr, ragn_uri, ciudadDestino, ciudadIATA, dataIda, dataVuelta, precioHotel, estrellas, roomQuantity, adults, radius):

    logger.info('Iniciamos busqueda en agente de informacion')

    global mss_cnt

    # Graph para buscador
    gmess = Graph()
    gmess.bind('myns_pet', myns_pet)
    gmess.bind('myns_atr', myns_atr)
    
    busqueda = myns_pet["ConsultarOpcionesAlojamiento"]

    gmess.add((busqueda, myns_par.ciudadDestino, Literal(ciudadDestino)))
    gmess.add((busqueda, myns_par.ciudadIATA, Literal(ciudadIATA)))
    gmess.add((busqueda, myns_par.dataIda, Literal(dataIda)))
    gmess.add((busqueda, myns_par.dataVuelta, Literal(dataVuelta)))
    gmess.add((busqueda, myns_par.precioHotel, Literal(precioHotel)))      
    gmess.add((busqueda, myns_par.estrellas, Literal(estrellas)))
    gmess.add((busqueda, myns_par.roomQuantity, Literal(roomQuantity)))
    gmess.add((busqueda, myns_par.adults, Literal(adults)))
    gmess.add((busqueda, myns_par.radius, Literal(radius)))

    gmess.bind('foaf', FOAF)
    gmess.bind('dso', DSO)
    req_obj = agn[AgenteAlojamiento.name + '-InfoAgent']
    gmess.add((req_obj, RDF.type, DSO.InfoAgent))
    gmess.add((req_obj, DSO.AgentType, DSO.HotelsAgent))
    

    msg = build_message(gmess, perf=ACL.request,
                      sender=AgenteAlojamiento.uri,
                      receiver=ragn_uri,
                      content=req_obj,
                      msgcnt=mss_cnt)

    gr = send_message(msg, addr)
    
    mss_cnt += 1

    logger.info('Alojamientos recibidos')
    
    return gr


if __name__ == '__main__':
    # Ponemos en marcha los behaviors
    #ab1 = Process(target=agentbehavior1)
    #ab1.start()

    # Ponemos en marcha el servidor
    app.run(host=hostname, port=port)

    # Esperamos a que acaben los behaviors
    #ab1.join()
    logger.info('The End')
