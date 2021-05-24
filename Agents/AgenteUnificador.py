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
    maxPrecio = request.form['maxPrecio']
    minPrecio = request.form['minPrecio']
    estrellas = request.form['estrellas']
    hotelData= {
        'ciudadOrigen' : ciudadOrigen,
        'ciudadDestino' : ciudadDestino,
        'dataIda' : dataIda,
        'dataVuelta' : dataVuelta,
        'maxPrecio' : maxPrecio,
        'minPrecio' : minPrecio,
        'estrellas' : estrellas
    }

    gm = pedirSelecciónAlojamiento(ciudadDestino, dataIda, dataVuelta, maxPrecio, minPrecio, estrellas)


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

def pedirSelecciónAlojamiento(ciudadDestino, dataIda, dataVuelta, maxPrecio, minPrecio, estrellas):

    global mss_cnt
    logger.info('Iniciamos busqueda de alojamiento')

    gmess = Graph()
    gmess.bind('myns_pet', myns_pet)
    gmess.bind('myns_atr', myns_atr)

    peticion = myns_pet["SolicitarSelecciónAlojamiento"]

    gmess.add((peticion, myns_atr.ciudadDestino, Literal(ciudadDestino)))
    gmess.add((peticion, myns_atr.dataIda, Literal(dataIda)))
    gmess.add((peticion, myns_atr.dataVuelta, Literal(dataVuelta)))
    gmess.add((peticion, myns_atr.maxPrecio, Literal(maxPrecio)))
    gmess.add((peticion, myns_atr.minPrecio, Literal(minPrecio)))
    gmess.add((peticion, myns_atr.estrellas, Literal(estrellas)))

    
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


if __name__ == '__main__':
    # Ponemos en marcha los behaviors
    # ab1 = Process(target=agentbehavior1, args=(cola1,))
    # ab1.start()

    # Ponemos en marcha el servidor
    app.run(host=hostname, port=port)

    # Esperamos a que acaben los behaviors
    # ab1.join()
    print('The End')
