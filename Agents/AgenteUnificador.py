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

agn = Namespace("http://www.agentes.org#")

# Datos del agente unificador
AgenteUnificador = Agent('AgenteUnificador',
                         agn.AgenteUnificador,
                         'http://%s:%d/comm' % (hostaddr, port),
                         'http://%s:%d/Stop' % (hostaddr, port))

# Datos del agente gestor de transporte
GestorTransporte = Agent("GestorTransporte",
                         agn.GestorTransporte,
                         "http://%s:%d/comm" % (hostaddr, PUERTO_GESTOR_TRANSPORTE),
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
                     args=(ciudadOrigen, ciudadDestino, fechaIda, fechaVuelta, presupuestoVuelo, return_dic))

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
        graph_trans = return_dic["transporte"]
        graph_aloj = return_dic["alojamiento"]
        graph_act = return_dic["actividades"]

        # Obtenemos la performativa de los mensajes en los tres casos
        msgdic_trans = get_message_properties(graph_trans)
        msgdic_aloj = get_message_properties(graph_aloj)
        msgdic_act = get_message_properties(graph_act)

        perf_trans = msgdic_trans["performative"]
        perf_aloj = msgdic_aloj["performative"]
        perf_act = msgdic_act["performative"]

        if perf_trans == ACL.failure or perf_aloj == ACL.failure or perf_act == ACL.failure:
            displayData = {
                "error": 1,
                "errorMessage": "Parámetros de entrada no válidos."
            }
        elif perf_trans == ACL.cancel or perf_aloj == ACL.cancel or perf_act == ACL.cancel:
            displayData = {
                "error": 1,
                "errorMessage": "No se ha encontrado ningún agente de información."
            }
        else:
            gsearch = graph_trans.triples((None, agn.esUn, agn.Billete))
            billete = next(gsearch)[0]

            id_billete = graph_trans.value(subject=billete, predicate=agn.Id)
            hora_salida_billete = graph_trans.value(subject=billete, predicate=agn.DiaHoraSalida)
            hora_llegada_billete = graph_trans.value(subject=billete, predicate=agn.DiaHoraLlegada)
            asiento_billete = graph_trans.value(subject=billete, predicate=agn.Asiento)
            clase_billete = graph_trans.value(subject=billete, predicate=agn.Clase)
            precio_billete = graph_trans.value(subject=billete, predicate=agn.Precio)

            gsearch = graph_aloj.triples((None, agn.esUn, agn.Hotel))
            alojamiento = next(gsearch)[0]
            nombre_aloj = graph_aloj.value(subject=alojamiento, predicate=agn.Nombre)
            direccion_aloj = graph_aloj.value(subject=alojamiento, predicate=agn.Direccion)
            precio_aloj = graph_aloj.value(subject=alojamiento, predicate=agn.Precio)

            # TODO: Coger y mostrar la información de más de una actividad
            gsearch = graph_act.triples((None, agn.esUn, agn.activity))
            actividad = next(gsearch)[0]
            nombre_act = graph_act.value(subject=actividad, predicate=agn.nombre)

            displayData = {
                "error": 0,
                "ciudadOrigen": ciudadOrigen,
                "ciudadDestino": ciudadDestino,
                "fechaIda": fechaIda,
                "fechaVuelta": fechaVuelta,
                "idBillete": id_billete,
                "horaSalidaBillete": hora_salida_billete,
                "horaLlegadaBillete": hora_llegada_billete,
                "asientoBillete": asiento_billete,
                "claseBillete": clase_billete,
                "precioBillete": precio_billete,
                "nombreAloj": nombre_aloj,
                "direccionAloj": direccion_aloj,
                "precioAloj": precio_aloj,
                "nombreActividad": nombre_act
            }

    except Exception as e:
        logger.error(str(e))
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


def pedirSeleccionTransporte(ciudadOrigen, ciudadDestino, fechaIda, fechaVuelta, presupuestoVuelo, return_dic):
    global mss_cnt

    logger.info('Pide selección de transporte.')

    msg_graph = Graph()

    # Vinculamos todos los espacios de nombres a utilizar
    msg_graph.bind("agn", agn)

    # Construimos el mensaje de petición
    selection_req = agn["AgenteUnificador-SeleccionTransporte"]
    msg_graph.add((selection_req, agn.originCity, Literal(ciudadOrigen)))
    msg_graph.add((selection_req, agn.destinationCity, Literal(ciudadDestino)))
    msg_graph.add((selection_req, agn.departureDate, Literal(fechaIda)))
    msg_graph.add((selection_req, agn.comebackDate, Literal(fechaVuelta)))
    msg_graph.add((selection_req, agn.budget, Literal(presupuestoVuelo)))

    res_graph = send_message(build_message(msg_graph,
                                           ACL.request,
                                           sender=AgenteUnificador.uri,
                                           receiver=GestorTransporte.uri,
                                           content=selection_req,
                                           msgcnt=mss_cnt), GestorTransporte.address)

    mss_cnt += 1

    return_dic["transporte"] = res_graph

    logger.info("Selección de transporte recibida.")


def pedirSeleccionAlojamiento(ciudadDestino, fechaIda, fechaVuelta, presupuestoAloj, estrellas, nhabitaciones, npersonas, dcentro,
            return_dic):
    global mss_cnt

    logger.info('Iniciamos busqueda de alojamiento')

    msg_graph = Graph()

    # Vinculamos todos los espacios de nombres a utilizar
    msg_graph.bind("agn", agn)

    # Construimos el mensaje de petición
    selection_req = agn["AgenteUnificador-SeleccionAlojamiento"]
    msg_graph.add((selection_req, agn.destinationCity, Literal(ciudadDestino)))
    msg_graph.add((selection_req, agn.departureDate, Literal(fechaIda)))
    msg_graph.add((selection_req, agn.comebackDate, Literal(fechaVuelta)))
    msg_graph.add((selection_req, agn.hotelBudget, Literal(presupuestoAloj)))
    msg_graph.add((selection_req, agn.ratings, Literal(estrellas)))
    msg_graph.add((selection_req, agn.roomQuantity, Literal(nhabitaciones)))
    msg_graph.add((selection_req, agn.adults, Literal(npersonas)))
    msg_graph.add((selection_req, agn.radius, Literal(dcentro)))

    msg = build_message(msg_graph,
                        perf=ACL.request,
                        sender=AgenteUnificador.uri,
                        receiver=GestorAlojamiento.uri,
                        content=selection_req,
                        msgcnt=mss_cnt)

    res_graph = send_message(msg, GestorAlojamiento.address)

    mss_cnt += 1

    logger.info('Alojamiento recibido')

    return_dic["alojamiento"] = res_graph


def pedirSeleccionActividades(ciudadDestino, dataIda, dataVuelta, precioHotel, estrellas, roomQuantity, adults, radius,
                              return_dic):
    global mss_cnt
    logger.info('Iniciamos busqueda de actividades')

    msg_graph = Graph()

    # Vinculamos todos los espacios de nombres a utilizar
    msg_graph.bind("agn", agn)

    # Construimos el mensaje de petición
    selection_req = agn["AgenteUnificador-SeleccionActividades"]
    msg_graph.add((selection_req, agn.ciudadDestino, Literal(ciudadDestino)))
    msg_graph.add((selection_req, agn.radius, Literal(radius)))

    msg = build_message(msg_graph, perf=ACL.request,
                        sender=AgenteUnificador.uri,
                        receiver=GestorActividades.uri,
                        content=selection_req,
                        msgcnt=mss_cnt)

    res_graph = send_message(msg, GestorActividades.address)

    mss_cnt += 1

    logger.info('Actividades recibidas')

    return_dic['actividades'] = res_graph


if __name__ == "__main__":
    # Ponemos en marcha el servidor Flask
    app.run(host=hostname, port=port)
    logger.info("The end.")
