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

from multiprocessing import Process, Queue
import re
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
from AgentUtil.CodigosIATA import IATA

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
    port = 9001
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
    dhostname = gethostname()
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

# Contador de mensajes
mss_cnt = 0

# Datos del Agente
AgenteUnificador = Agent('AgenteUnificador',
                       agn.AgenteUnificador,
                       'http://%s:%d/comm' % (hostaddr, port),
                       'http://%s:%d/Stop' % (hostaddr, port))

AgenteAlojamiento = Agent('AgenteAlojamiento',
                       agn.AgenteAlojamiento,
                       'http://%s:%d/comm' % (hostaddr, 9002),
                       'http://%s:%d/Stop' % (hostaddr, 9002))

AgenteActividades = Agent('AgenteActividades',
                       agn.AgenteActividades,
                       'http://%s:%d/comm' % (hostaddr, 9007),
                       'http://%s:%d/Stop' % (hostaddr, 9007))


# Global triplestore graph
dsgraph = Graph()

cola1 = Queue()

# Flask stuff
app = Flask(__name__)
if not args.verbose:
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

@app.route("/")
def main():
    return render_template('mainPage.html')

@app.route("/", methods=['POST'])
def peticionPlan():

    ciudadOrigen = request.form['ciudadOrigen']
    ciudadDestino = request.form['ciudadDestino']
    dataIda = request.form['dataIda']
    dataVuelta = request.form['dataVuelta']
    precioHotel = request.form['precioHotel']
    estrellas = request.form['estrellas']
    roomQuantity = request.form['roomQuantity']
    adults = request.form['adults']
    radius = request.form['radius']
    
    try:
        errorMessage = ''
        ciudadIATA_origen = convertirIATA(ciudadOrigen)
        ciudadIATA_destino = convertirIATA(ciudadDestino)
        nombre=''
        direccion=''
        actividad=''
        gmAlojamiento = pedirSelecciónAlojamiento(ciudadIATA_destino, ciudadDestino, dataIda, dataVuelta, precioHotel, estrellas, roomQuantity, adults, radius)
        gmActividad = pedirSeleccionActividades(ciudadIATA_destino, ciudadDestino, dataIda, dataVuelta, precioHotel, estrellas, roomQuantity, adults, radius)
            
        msgdicAlojamiento = get_message_properties(gmAlojamiento)
        msgdicActividad = get_message_properties(gmActividad)
        perfAlojamiento = msgdicAlojamiento['performative']
        perfActividad = msgdicActividad['performative']
        if(perfAlojamiento == ACL.failure or perfActividad== ACL.failure):
            hotelData = {
            'error': 1,
            'errorMessage': 'Parametros de entrada no válidos'
        }
        elif(perfAlojamiento == ACL.cancel or perfActividad == ACL.cancel):
            hotelData = {
            'error': 1,
            'errorMessage': 'Ningún agente de información encontrado'
        }
        else:
            for s,p,o in gmAlojamiento.triples((None, myns_atr.esUn, myns.hotel)):
                nombre = gmAlojamiento.value(subject=s, predicate=myns_atr.nombre)
                direccion = gmAlojamiento.value(subject=s, predicate=myns_atr.direccion)
            
            for s,p,o in gmActividad.triples((None, myns_atr.esUn, myns.activity)):
                actividad = gmActividad.value(subject=s, predicate=myns_atr.nombre)

            hotelData= {
                'ciudadOrigen' : ciudadOrigen,
                'ciudadDestino' : ciudadDestino,
                'dataIda' : dataIda,
                'dataVuelta' : dataVuelta,
                'nombreHotel': nombre,
                'direccion' : direccion,
                'nombreActividad': actividad,
                'error': 0
            }
    except:
        hotelData = {
            'error': 1,
            'errorMessage': 'Error de conexión entre agentes'
        }
    finally:
        return render_template('processingPlan.html', hotelData=hotelData)


@app.route("/comm")
def comunicacion():
    """
    Entrypoint de comunicacion
    """
    global dsgraph
    global mss_cnt
    pass


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


def agentbehavior1(cola):
    """
    Un comportamiento del agente

    :return:
    """
    pass

def pedirSelecciónAlojamiento(ciudadIATA_destino, ciudadDestino, dataIda, dataVuelta, precioHotel, estrellas, roomQuantity, adults, radius):

    global mss_cnt
    logger.info('Iniciamos busqueda de alojamiento')

    gmess = Graph()
    gmess.bind('myns_pet', myns_pet)
    gmess.bind('myns_atr', myns_atr)

    peticion = myns_pet["SolicitarSelecciónAlojamiento"]

    gmess.add((peticion, myns_atr.ciudadIATA_destino, Literal(ciudadIATA_destino)))
    gmess.add((peticion, myns_atr.ciudadDestino, Literal(ciudadDestino)))
    gmess.add((peticion, myns_atr.dataIda, Literal(dataIda)))
    gmess.add((peticion, myns_atr.dataVuelta, Literal(dataVuelta)))
    gmess.add((peticion, myns_atr.precioHotel, Literal(precioHotel)))
    gmess.add((peticion, myns_atr.estrellas, Literal(estrellas)))
    gmess.add((peticion, myns_atr.roomQuantity, Literal(roomQuantity)))
    gmess.add((peticion, myns_atr.adults, Literal(adults)))
    gmess.add((peticion, myns_atr.radius, Literal(radius)))

    
    gmess.bind('foaf', FOAF)
    gmess.bind('dso', DSO)
    req_obj = agn[AgenteUnificador.name + '-SolverAgent']
    gmess.add((req_obj, RDF.type, DSO.SolverAgent))
    gmess.add((req_obj, DSO.AgentType, DSO.PersonalAgent))
    

    msg = build_message(gmess, perf=ACL.request,
                      sender=AgenteUnificador.uri,
                      receiver=AgenteAlojamiento.uri,
                      content=req_obj,
                      msgcnt=mss_cnt)
    
    gr = send_message(msg, AgenteAlojamiento.address)
    
    mss_cnt += 1

    logger.info('Alojamientos recibidos')
    
    return gr

def pedirSeleccionActividades(ciudadIATA_destino, ciudadDestino, dataIda, dataVuelta, precioHotel, estrellas, roomQuantity, adults, radius):

    global mss_cnt
    logger.info('Iniciamos busqueda de actividades')

    gmess = Graph()
    gmess.bind('myns_pet', myns_pet)
    gmess.bind('myns_atr', myns_atr)

    peticion = myns_pet["SolicitarSeleccionActividades"]

    gmess.add((peticion, myns_atr.ciudadIATA_destino, Literal(ciudadIATA_destino)))
    gmess.add((peticion, myns_atr.ciudadDestino, Literal(ciudadDestino)))
    gmess.add((peticion, myns_atr.dataIda, Literal(dataIda)))
    gmess.add((peticion, myns_atr.dataVuelta, Literal(dataVuelta)))
    gmess.add((peticion, myns_atr.precioHotel, Literal(precioHotel)))
    gmess.add((peticion, myns_atr.estrellas, Literal(estrellas)))
    gmess.add((peticion, myns_atr.roomQuantity, Literal(roomQuantity)))
    gmess.add((peticion, myns_atr.adults, Literal(adults)))
    gmess.add((peticion, myns_atr.radius, Literal(radius)))

    
    gmess.bind('foaf', FOAF)
    gmess.bind('dso', DSO)
    req_obj = agn[AgenteUnificador.name + '-SolverAgent']
    gmess.add((req_obj, RDF.type, DSO.SolverAgent))
    gmess.add((req_obj, DSO.AgentType, DSO.PersonalAgent))
    

    msg = build_message(gmess, perf=ACL.request,
                      sender=AgenteUnificador.uri,
                      receiver=AgenteActividades.uri,
                      content=req_obj,
                      msgcnt=mss_cnt)
    
    gr = send_message(msg, AgenteActividades.address)
    
    mss_cnt += 1

    logger.info('Actividades recibidas')
    
    return gr

def convertirIATA(ciudad):
    try:
        codigo = IATA[str(ciudad)]
    except:
        codigo = ''
    finally:
        return codigo


if __name__ == '__main__':
    # Ponemos en marcha los behaviors
    # ab1 = Process(target=agentbehavior1, args=(cola1,))
    # ab1.start()

    # Ponemos en marcha el servidor
    app.run(host=hostname, port=port)

    # Esperamos a que acaben los behaviors
    # ab1.join()
    print('The End')
