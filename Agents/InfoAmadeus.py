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

from amadeus import Client, ResponseError
from AgentUtil.APIKeys import AMADEUS_KEY, AMADEUS_SECRET
from pprint import PrettyPrinter

from Agents.AgenteUnificador import AgenteAlojamiento
from multiprocessing import Process, Queue
import socket
import logging
import argparse
import requests


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

AMADEUS_END_POINT = 'https://test.api.amadeus.com/v2/shopping/hotel-offers'

amadeus = Client(
    client_id=AMADEUS_KEY,
    client_secret=AMADEUS_SECRET
)

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
    port = 9003
else:
    port = args.port

if args.open:
    hostname = '0.0.0.0'
    hostaddr = gethostname()
else:
    hostaddr = hostname = socket.gethostname()

print('Hostname =', hostaddr)

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
myns = Namespace("http://www.agentes.org/")
myns_pet = Namespace("http://www.agentes.org/peticiones/")
myns_atr = Namespace("http://www.agentes.org/atributos/")
myns_par = Namespace("http://my.namespace.org/parametros/")
myns_hot = Namespace("http://my.namespace.org/hoteles/")

# Contador de mensajes
mss_cnt = 0

InfoAmadeus = Agent('InfoAmadeus',
                       agn.InfoAmadeus,
                       'http://%s:%d/comm' % (hostaddr, port),
                       'http://%s:%d/Stop' % (hostaddr, port))

print("DS hostname: ", dhostname)
print("DS port: ", dport)
# Directory agent address
DirectoryAgent = Agent('DirectoryAgent',
                       agn.Directory,
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
    reg_obj = agn[InfoAmadeus.name + '-Register']
    gmess.add((reg_obj, RDF.type, DSO.Register))
    gmess.add((reg_obj, DSO.Uri, InfoAmadeus.uri))
    gmess.add((reg_obj, FOAF.name, Literal(InfoAmadeus.name)))
    gmess.add((reg_obj, DSO.Address, Literal(InfoAmadeus.address)))
    gmess.add((reg_obj, DSO.AgentType, DSO.HotelsAgent))

    # Lo metemos en un envoltorio FIPA-ACL y lo enviamos
    gr = send_message(
        build_message(gmess, perf=ACL.request,
                      sender=InfoAmadeus.uri,
                      receiver=DirectoryAgent.uri,
                      content=reg_obj,
                      msgcnt=mss_cnt),
        DirectoryAgent.address)
    mss_cnt += 1

    return gr

@app.route("/")
def hello():
    return "Agente InfoAmadeus en marcha!"


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

            if accion == DSO.InfoAgent:
                gr = infoHoteles(gm, msgdic)
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


def agentbehavior1():
    """
    Un comportamiento del agente
    :return:
    """
    # Registramos el agente
    gr = register_message()
    pass

def infoHoteles(gm, msgdic):
    busqueda = myns_pet["ConsultarOpcionesAlojamiento"]

    ciudadDestino = gm.value(subject= busqueda, predicate= myns_par.ciudadDestino)
    ciudadIATA = gm.value(subject= busqueda, predicate= myns_par.ciudadIATA)
    dataIda = gm.value(subject= busqueda, predicate= myns_par.dataIda)
    dataVuelta = gm.value(subject= busqueda, predicate= myns_par.dataVuelta)
    precioHotel = gm.value(subject= busqueda, predicate= myns_par.precioHotel)
    estrellas = gm.value(subject= busqueda, predicate= myns_par.estrellas)
    roomQuantity = gm.value(subject= busqueda, predicate= myns_par.roomQuantity)
    adults = gm.value(subject= busqueda, predicate= myns_par.adults)
    radius = gm.value(subject= busqueda, predicate= myns_par.radius)

    gr = Graph()
    try:                  
        #response = amadeus.shopping.hotel_offers.get(cityCode=str(ciudadDestino), 
        #                                           checkInDate=str(dataIda), 
        #                                            checkOutDate=str(dataVuelta),
        #                                            roomQuantity=int(roomQuantity),
        #                                            adults=int(adults),
        #                                            radius=int(radius),
        #                                            ratings=int(estrellas),
        #                                            priceRange=str(precioHotel),
        #                                            currency='EUR'
        #                                            )
        response = amadeus.shopping.hotel_offers.get(cityCode=str(ciudadIATA))
                
        
        gr.bind('myns_hot', myns_hot)

        for h in response.data:
            hotel = h['hotel']['hotelId']
            hotel_obj = myns_hot[hotel]
            gr.add((hotel_obj, myns_atr.esUn, myns.hotel))
            gr.add((hotel_obj, myns_atr.nombre, Literal(h['hotel']['name'])))

            # Aqui realizariamos lo que pide la accion
            # Por ahora simplemente retornamos un Inform-done
            gr = build_message(gr,
                            ACL['confirm'],
                            sender=InfoAmadeus.uri,
                            msgcnt=mss_cnt,
                            receiver=msgdic['sender'], )
    except:
        logger.info('Parameters not correct')
        gr = build_message(gr,
                            ACL['failure'],
                            sender=InfoAmadeus.uri,
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