# -*- coding: utf-8 -*-
"""
Agente de información de transportes. Se registra en el directorio de agentes como ello.
"""

from multiprocessing import Process, Queue
import logging
import argparse

from flask import Flask, request
from rdflib import Graph, Namespace, Literal
from rdflib.namespace import FOAF, RDF

from AgentUtil.ACL import ACL
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
    port = 9001
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

# Datos del agente de información de transporte
InfoAgent = Agent("TransportInfoAgent",
                  agn.AgentInfo,
                  "http://%s:%d/Comm" % (hostaddr, port),
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

# Cola de comunicación entre procesos
queue1 = Queue()


# ENTRY POINTS
@app.route("/Comm")
def comunication():
    """
    Entry point de comunicación con el agente.

    Retorna un objeto que representa el resultado de una búsqueda de transporte.

    Asumimos que se reciben siempre acciones correctas, que se refieren a lo que puede hacer el agente, y que las
    acciones se reciben en un mensaje de tipo ACL.request.
    """
    global igraph
    global mss_cnt

    logger.info("Petición de información de transporte recibida.")

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
                                  sender=InfoAgent.uri,
                                  msgcnt=mss_cnt)
    elif msgdic["performative"] != ACL.request:
        # Si la performativa no es de tipo 'request', respondemos que no hemos entendido el mensaje
        res_graph = build_message(Graph(),
                                  ACL["not-understood"],
                                  sender=InfoAgent.uri,
                                  msgcnt=mss_cnt)
    else:
        res_graph = get_flights(msg_graph, msgdic)

    mss_cnt += 1

    logger.info("El agente de información de transporte responde a la petición.")

    return res_graph.serialize(format="xml")


@app.route("/Info")
def info():
    """
    Entrada que da información del estado del agente.
    """
    return "El agente de información de transporte está funcionando."


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


def get_flights(msg_graph, msgdic):
    """
    Retorna un mensaje en formato FIPA-ACL, de tipo 'inform', que contiene el resultado de la búsqueda de vuelos hecha
    en la API Amadeus con los criterio de búsqueda que hay en el grafo msg_graph, pasado como parámetro. En caso de
    producirse un error, la función retorna un mensaje FIPA-ACL de tipo 'failure'.
    """
    res_graph = Graph()

    # Extraemos los campos de búsqueda del contenido del mensaje, una vez que este está expresado como un grafo
    search_req = agn["GestorTransporte-InfoSearch"]
    originLocationName = msg_graph.value(subject=search_req, predicate=agn.originCity)
    originLocationCode = convert_to_IATA(str(originLocationName))
    destinationLocationName = msg_graph.value(subject=search_req, predicate=agn.destinationCity)
    destinationLocationCode = convert_to_IATA(str(destinationLocationName))
    departureDate = msg_graph.value(subject=search_req, predicate=agn.departureDate)
    budget = msg_graph.value(subject=search_req, predicate=agn.budget)

    amadeus = Client(
        client_id=AMADEUS_KEY,
        client_secret=AMADEUS_SECRET
    )

    try:
        # Hace la búsqueda a la API Amadeus a través de su librería y guarda el resultado en formato JSON (accesible
        # como si fuera un diccionario Python)
        response = amadeus.shopping.flight_offers_search.get(
            originLocationCode=originLocationCode,
            destinationLocationCode=destinationLocationCode,
            departureDate=departureDate,
            adults=1)

        for ticket in response.data:
            # La librería de Amadeus no deja filtrar por presupuesto directamente, así que lo hacemos manualmente
            # una vez obtenemos los resultados
            if float(ticket["price"]["total"]) <= float(budget):
                # Extrae los campos que nos interesan de la respuesta dada por la API.
                #
                # Para determinar la fecha de salida y la fecha de llegada solo consideramos el primer segmento del
                # vuelo, en el caso de un vuelo con escalas. Solo consideramos una tipo (clase) de billete, en el
                # caso que haya más de uno.
                ticketId = int(ticket["id"])
                departureDate = ticket["itineraries"][0]["segments"][0]["departure"]["at"]
                arrivalDate = ticket["itineraries"][0]["segments"][0]["arrival"]["at"]
                ticketClass = ticket["travelerPricings"][0]["fareOption"]
                ticketPrice = float(ticket["price"]["total"])

                # Construye el grafo que enviaremos como respuesta al agente
                ticket_obj = agn["Billete" + str(ticketId)]
                res_graph.add((ticket_obj, agn.Id, Literal(ticketId)))
                res_graph.add((ticket_obj, agn.origenBillete, Literal(originLocationName)))
                res_graph.add((ticket_obj, agn.destinoBillete, Literal(destinationLocationName)))
                res_graph.add((ticket_obj, agn.DiaHoraSalida, Literal(departureDate)))
                res_graph.add((ticket_obj, agn.DiaHoraLlegada, Literal(arrivalDate)))
                res_graph.add((ticket_obj, agn.Asiento, Literal("A23")))  # Asiento inventado
                res_graph.add((ticket_obj, agn.Clase, Literal(ticketClass)))
                res_graph.add((ticket_obj, agn.Precio, Literal(ticketPrice)))

                res_graph = build_message(res_graph,
                                          ACL["inform"],
                                          sender=InfoAgent.uri,
                                          receiver=msgdic['sender'],
                                          msgcnt=mss_cnt)

    except ResponseError as error:
        logger.info(error)
        res_graph = build_message(res_graph,
                                  ACL["failure"],
                                  sender=InfoAgent.uri,
                                  receiver=msgdic['sender'],
                                  msgcnt=mss_cnt)
    finally:
        return res_graph


def register_message():
    """
    Envia un mensaje de registro al servicio de registro usando una performativa 'Request' con
    una acción 'Register' del servicio de directorio.
    """
    global mss_cnt

    logger.info("Registro agente información de transporte.")

    msg_graph = Graph()

    # Vinculamos los espacios de nombres que usaremos para construir el mensaje de registro
    msg_graph.bind("rdf", RDF)
    msg_graph.bind("foaf", FOAF)
    msg_graph.bind("dso", DSO)

    # Construimos el mensaje de registro
    reg_obj = agn[InfoAgent.name + "-Register"]
    msg_graph.add((reg_obj, RDF.type, DSO.Register))
    msg_graph.add((reg_obj, DSO.Uri, InfoAgent.uri))
    msg_graph.add((reg_obj, FOAF.name, Literal(InfoAgent.name)))
    msg_graph.add((reg_obj, DSO.Address, Literal(InfoAgent.address)))
    msg_graph.add((reg_obj, DSO.AgentType, DSO.FlightsAgent))

    res_graph = send_message(
        build_message(msg_graph,
                      ACL.request,
                      sender=InfoAgent.uri,
                      receiver=DirectoryAgent.uri,
                      content=reg_obj,
                      msgcnt=mss_cnt),
        DirectoryAgent.address)

    mss_cnt += 1
    return res_graph


def agentbehaviour1(queue):
    """
    Esta función se ejecuta en paralelo al servidor Flask. Hace el registro en el directorio y luego espera mensajes
    de una cola y los imprime por pantalla hasta que llega un 0.
    """
    # Registramos el agente
    res_graph = register_message()

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
