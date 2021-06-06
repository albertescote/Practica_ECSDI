# -*- coding: utf-8 -*-
"""
Agente que recibe la petición del plan de viaje e inicia las acciones para obtener una propuesta de plan de viaje.
Posteriormente, junta todas las partes del plan y se lo envia al usuario.
"""

import argparse
import logging
import multiprocessing
import socket
from multiprocessing import Process

from flask import Flask, request, render_template
from rdflib import Graph, RDF, Namespace, Literal
from rdflib.namespace import FOAF

from AgentUtil.ACL import ACL
from AgentUtil.ACLMessages import build_message, send_message, get_message_properties
from AgentUtil.Agent import Agent
from AgentUtil.AgentsPorts import PUERTO_UNIFICADOR, PUERTO_GESTOR_ALOJAMIENTO, \
    PUERTO_GESTOR_ACTIVIDADES, PUERTO_GESTOR_TRANSPORTE
from AgentUtil.DSO import DSO
from AgentUtil.FlaskServer import shutdown_server
from AgentUtil.Logging import config_logger
from AgentUtil.Util import gethostname
from Agents.GestorAlojamiento import GestorAlojamiento

# Definimos los parámetros de la linea de comandos
parser = argparse.ArgumentParser()
parser.add_argument("--open", help="Define si el servidor está abierto al exterior o no.", action="store_true",
                    default=False)
parser.add_argument("--port", type=int, help="Puerto de comunicación del agente.")
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
    port = PUERTO_UNIFICADOR
else:
    port = args.port

if not args.verbose:
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)

agn = Namespace("http://www.agentes.org/")
myns = Namespace("http://www.agentes.org/")
myns_pet = Namespace("http://www.agentes.org/peticiones/")
myns_atr = Namespace("http://www.agentes.org/atributos/")

# Datos del agente unificador
AgenteUnificador = Agent('AgenteUnificador',
                         agn.AgenteUnificador,
                         'http://%s:%d/comm' % (hostaddr, port),
                         'http://%s:%d/Stop' % (hostaddr, port))

# Datos del agente gestor de transporte
GestorTransporte = Agent("GestorTransporte",
                         agn.GestorTransporte,
                         "http://%s:%d/Comm" % (hostaddr, PUERTO_GESTOR_TRANSPORTE),
                         "http://%s:%d/Stop" % (hostaddr, PUERTO_GESTOR_TRANSPORTE))

# Datos del agente gestor de alojamiento
GestorAlojamiento = Agent('GestorAlojamiento',
                          agn.GestorAlojamiento,
                          'http://%s:%d/comm' % (hostaddr, PUERTO_GESTOR_ALOJAMIENTO),
                          'http://%s:%d/Stop' % (hostaddr, PUERTO_GESTOR_ALOJAMIENTO))

# Datos del agente gestor de actividades
GestorActividades = Agent('GestorActividades',
                          agn.GestorActividades,
                          'http://%s:%d/comm' % (hostaddr, PUERTO_GESTOR_ACTIVIDADES),
                          'http://%s:%d/Stop' % (hostaddr, PUERTO_GESTOR_ACTIVIDADES))

# Grafo de estado del agente
augraph = Graph()

# Instanciamos el servidor Flask
app = Flask(__name__)

# Contador de mensajes
mss_cnt = 0


# ENTRY POINTS
@app.route("/")
def main():
    return render_template('mainPage.html')


@app.route("/", methods=['POST'])
def peticionPlan():
    # Extraemos el valor de los campos del formulario

    # General
    ciudadOrigen = request.form['ciudadOrigen']
    ciudadDestino = request.form['ciudadDestino']
    fechaIda = request.form['fechaIda']
    fechaVuelta = request.form['fechaVuelta']

    # Vuelo
    presupuestoVuelo = request.form['presupuestoVuelo']

    # Alojamiento
    npersonas = request.form['npersonas']
    nhabitaciones = request.form['nhabitaciones']
    estrellas = request.form['estrellas']
    dcentro = request.form['dcentro']
    presupuestoAloj = request.form['presupuestoAloj']

    displayData = None

    try:
        # Ejecuta la selección de transporte, alojamiento y actividades en tres procesos diferentes para obtener
        # paralelismo. Los tres procesos comparten un objeto llamado 'return_dic'.
        manager = multiprocessing.Manager()
        return_dic = manager.dict()
        p1 = Process(target=pedirSeleccionAlojamiento, args=(
            ciudadDestino, fechaIda, fechaVuelta, presupuestoAloj, estrellas, nhabitaciones, npersonas, dcentro,
            return_dic))
        p2 = Process(target=pedirSeleccionActividades, args=(
            ciudadDestino, fechaIda, fechaVuelta, presupuestoAloj, estrellas, nhabitaciones, npersonas, dcentro,
            return_dic))
        p3 = Process(target=pedirSeleccionTransporte,
                     args=(ciudadDestino, ciudadOrigen, npersonas, presupuestoVuelo, return_dic))

        # Ejecuta los procesos
        p1.start()
        p2.start()
        p3.start()

        # Espera hasta que los procesos hijo acaben
        p1.join()
        p2.join()
        p3.join()

        # Extraemos por separado, del objeto compartido por los procesos 'return_dic', los grafos con los resultados de
        # la selección de transporte, alojamiento y actividades.
        # graph_trans = return_dic["transporte"]
        graph_aloj = return_dic["alojamiento"]
        graph_act = return_dic["actividades"]

        # Obtenemos la performativa de los mensajes en los tres casos
        # msgdic_trans = get_message_properties(graph_trans)
        msgdic_aloj = get_message_properties(graph_aloj)
        msgdic_act = get_message_properties(graph_act)

        # perf_trans = msgdic_trans["performative"]
        perf_aloj = msgdic_aloj["performative"]
        perf_act = msgdic_act["performative"]

        if perf_aloj == ACL.failure or perf_act == ACL.failure:
            displayData = {
                "error": 1,
                "errorMessage": "Parámetros de entrada no válidos."
            }
        elif perf_aloj == ACL.cancel or perf_act == ACL.cancel:
            displayData = {
                "error": 1,
                "errorMessage": "No se ha encontrado ningún agente de información."
            }
        else:
            gsearch = graph_aloj.triples((None, myns_atr.esUn, myns.hotel))
            alojamiento = next(gsearch)[0]
            nombre_aloj = graph_aloj.value(subject=alojamiento, predicate=myns_atr.nombre)
            direccion_aloj = graph_aloj.value(subject=alojamiento, predicate=myns_atr.direccion)

            # TODO: Coger y mostrar la información de más de una actividad
            gsearch = graph_act.triples((None, myns_atr.esUn, myns.activity))
            actividad = next(gsearch)[0]
            nombre_act = graph_act.value(subject=actividad, predicate=myns_atr.nombre)

            displayData = {
                'error': 0,
                'ciudadOrigen': ciudadOrigen,
                'ciudadDestino': ciudadDestino,
                'fechaIda': fechaIda,
                'fechaVuelta': fechaVuelta,
                'nombreHotel': nombre_aloj,
                'direccion': direccion_aloj,
                'nombreActividad': nombre_act
            }
    except Exception as e:
        displayData = {
            "error": 1,
            "errorMessage": str(e)
        }
    finally:
        return render_template("processingPlan.html", displayData=displayData)


@app.route("/comm")
def comunication():
    """
    Entry point de comunicación con el agente.
    """
    pass


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


def pedirSeleccionTransporte(ciudadDestino, ciudadOrigen, adults, budget, return_dic):
    logger.info('Iniciamos busqueda de Transporte')
    gr = Graph()
    logger.info('Transporte recibido')
    return_dic['transporte'] = gr


def pedirSeleccionAlojamiento(ciudadDestino, dataIda, dataVuelta, precioHotel, estrellas, roomQuantity, adults, radius,
                              return_dic):
    global mss_cnt
    logger.info('Iniciamos busqueda de alojamiento')

    gmess = Graph()
    gmess.bind('myns_pet', myns_pet)
    gmess.bind('myns_atr', myns_atr)

    peticion = myns_pet["SolicitarSelecciónAlojamiento"]

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
                        receiver=GestorAlojamiento.uri,
                        content=req_obj,
                        msgcnt=mss_cnt)

    gr = send_message(msg, GestorAlojamiento.address)

    mss_cnt += 1

    logger.info('Alojamiento recibido')

    return_dic['alojamiento'] = gr


def pedirSeleccionActividades(ciudadDestino, dataIda, dataVuelta, precioHotel, estrellas, roomQuantity, adults, radius,
                              return_dic):
    global mss_cnt
    logger.info('Iniciamos busqueda de actividades')

    gmess = Graph()
    gmess.bind('myns_pet', myns_pet)
    gmess.bind('myns_atr', myns_atr)

    peticion = myns_pet["SolicitarSeleccionActividades"]

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
                        receiver=GestorActividades.uri,
                        content=req_obj,
                        msgcnt=mss_cnt)

    gr = send_message(msg, GestorActividades.address)

    mss_cnt += 1

    logger.info('Actividades recibidas')

    return_dic['actividades'] = gr


if __name__ == "__main__":
    # Ponemos en marcha el servidor Flask
    app.run(host=hostname, port=port)
    logger.info("The end.")
