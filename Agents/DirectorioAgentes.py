# -*- coding: utf-8 -*-
"""
Agente que lleva el registro de otros agentes.

Utiliza un registro simple, que guarda en un grafo de estado RDF. El registro no es persistente y se mantiene
mientras el agente está ejecutándose.

Las acciones que se pueden utilizar están definidas en la ontología directory-service-ontology.owl.
"""


from multiprocessing import Process, Queue
import argparse
import logging

from flask import Flask, request, render_template
from rdflib import Graph, RDF, Namespace, RDFS, Literal
from rdflib.namespace import FOAF

from AgentUtil.ACL import ACL
from AgentUtil.FlaskServer import shutdown_server
from AgentUtil.Agent import Agent
from AgentUtil.ACLMessages import build_message, get_message_properties
from AgentUtil.Logging import config_logger
from AgentUtil.DSO import DSO
from AgentUtil.Util import gethostname
import socket

# Definimos los parámetros de la linea de comandos
parser = argparse.ArgumentParser()
parser.add_argument("--open", help="Define si el servidor está abierto al exterior o no.", action="store_true",
                    default=False)
parser.add_argument("--verbose", help="Genera un log de la comunicación del servidor web.", action="store_true",
                    default=False)
parser.add_argument("--port", type=int, help="Puerto de comunicación del agente.")

# Logging
logger = config_logger(level=1)

# Parsing de los parámetros de la línea de comandos
args = parser.parse_args()

# Configuración
if args.port is None:
    port = 9000
else:
    port = args.port

if args.open:
    hostname = "0.0.0.0"
    hostaddr = gethostname()
else:
    hostaddr = hostname = socket.gethostname()

# Directory Service Graph
dsgraph = Graph()

# Vinculamos todos los espacios de nombres a utilizar
dsgraph.bind("acl", ACL)
dsgraph.bind("rdf", RDF)
dsgraph.bind('rdfs', RDFS)
dsgraph.bind('foaf', FOAF)
dsgraph.bind("dso", DSO)

agn = Namespace("http://www.agentes.org#")
DirectoryAgent = Agent("DirectoryAgent",
                       agn.Directory,
                       "http://%s:%d/Register" % (hostaddr, port),
                       "http://%s:%d/Stop" % (hostaddr, port))

app = Flask(__name__)

if not args.verbose:
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)

mss_cnt = 0  # Message count

queue1 = Queue()  # Cola de comunicación entre procesos


# ENTRY POINTS
@app.route("/Register")
def register():
    """
    Entry point del agente que recibe los mensajes de registro y búsqueda.

    La respuesta es enviada al retornar la función, sin necesidad de enviar el mensaje explícitamente.

    Asumimos una versión simplificada del protocolo FIPA-request en la que no enviamos el mensaje Agree
    cuando vamos a responder.
    """
    def process_register():
        logger.info("Petición de registro recibida.")

        # Extraemos del campo 'content' del mensaje la dirección del agente, su nombre, su URI y su tipo
        agn_add = msg_graph.value(subject=content, predicate=DSO.Address)
        agn_name = msg_graph.value(subject=content, predicate=FOAF.name)
        agn_uri = msg_graph.value(subject=content, predicate=DSO.Uri)
        agn_type = msg_graph.value(subject=content, predicate=DSO.AgentType)

        # Añadimos la información en el grafo de registro vinculándola a la URI del agente y registrándola
        # como tipo FOAF.Agent
        dsgraph.add((agn_uri, RDF.type, FOAF.Agent))
        dsgraph.add((agn_uri, FOAF.name, agn_name))
        dsgraph.add((agn_uri, DSO.Address, agn_add))
        dsgraph.add((agn_uri, DSO.AgentType, agn_type))

        # Retornamos un mensaje de confirmación
        return build_message(Graph(),
                             ACL.confirm,
                             sender=DirectoryAgent.uri,
                             receiver=agn_uri,
                             msgcnt=mss_cnt)

    def process_search():
        # Solo consideramos la búsqueda por tipo de agente. Buscamos una coincidencia exacta y retornamos
        # la primera de las posibilidades, si hubiera más de una.
        logger.info("Petición de búsqueda recibida.")

        # Extraemos del campo 'content' el tipo de agente buscado
        agn_type = msg_graph.value(subject=content, predicate=DSO.AgentType)

        # Hacemos la búsqueda del agente con el tipo especificado en el grafo de estado del Directory Service
        search = dsgraph.triples((None, DSO.AgentType, agn_type))

        if search is not None:
            agn_uri = next(search)[0]
            agn_add = dsgraph.value(subject=agn_uri, predicate=DSO.Address)

            res_graph = Graph()
            res_graph.bind("dso", DSO)
            res_obj = agn["Directory-response"]
            res_graph.add((res_obj, DSO.Address, agn_add))
            res_graph.add((res_obj, DSO.Uri, agn_uri))

            # Retornamos un mensaje de respuesta, de tipo 'inform', con el objeto encontrado
            return build_message(res_graph,
                                 ACL.inform,
                                 sender=DirectoryAgent.uri,
                                 msgcnt=mss_cnt,
                                 receiver=agn_uri,
                                 content=res_obj)
        else:
            # Si no encontramos nada retornamos un mensaje de respuesta, de tipo 'inform', sin contenido
            return build_message(Graph(),
                                 ACL.inform,
                                 sender=DirectoryAgent.uri,
                                 msgcnt=mss_cnt)

    global dsgraph
    global mss_cnt

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
                                  sender=DirectoryAgent.uri,
                                  msgcnt=mss_cnt)
    elif msgdic["performative"] != ACL.request:
        # Si la performativa no es de tipo 'request', respondemos que no hemos entendido el mensaje
        res_graph = build_message(Graph(),
                                  ACL["not-understood"],
                                  sender=DirectoryAgent.uri,
                                  msgcnt=mss_cnt)
    else:
        # Extraemos el objeto del campo 'content', que ha de ser una acción de la ontología de registro
        content = msgdic["content"]
        # Averiguamos el tipo de la acción
        action = msg_graph.value(subject=content, predicate=RDF.type)

        # Acción de registro
        if action == DSO.Register:
            res_graph = process_register()
        # Acción de búsqueda
        elif action == DSO.Search:
            res_graph = process_search()
        # No había ninguna acción en el mensaje
        else:
            res_graph = build_message(Graph(),
                                      ACL["not-understood"],
                                      sender=DirectoryAgent.uri,
                                      msgcnt=mss_cnt)

    mss_cnt += 1
    return res_graph.serialize(format="xml")


@app.route("/Info")
def info():
    """
    Entrada que da información del estado del servicio de directorio. Retorna una página web (código HTML)
    que podemos visualizar en el navegador.
    """
    global dsgraph
    global mss_cnt

    return render_template("info.html", nmess=mss_cnt, graph=dsgraph.serialize(format="turtle"))


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


def agentbehaviour1(queue):
    """
    Esta función se ejecuta en paralelo al servidor Flask. Simplemente espera mensajes de una cola y los imprime
    por pantalla hasta que llega un 0.
    """
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
    app.run(host=hostname, port=port, debug=True)

    # Espera hasta que el proceso hijo termine
    ab1.join()
    logger.info("The end.")
