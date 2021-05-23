from amadeus import Client, ResponseError
from AgentUtil.APIKeys import AMADEUS_KEY, AMADEUS_SECRET
from pprint import PrettyPrinter

amadeus = Client(
    client_id=AMADEUS_KEY,
    client_secret=AMADEUS_SECRET
)
ppr = PrettyPrinter(indent=4)

# Hotels query
try:
    response = amadeus.shopping.hotel_offers.get(cityCode='BCN')
    print("-----------------------------------")
    print("HOTELS")
    print("-----------------------------------")
    for h in response.data:
        ppr.pprint(h['hotel']['name'])
    print('---')
    # Siguientes paginas de resultados
    response = amadeus.next(response)
    for h in response.data:
        ppr.pprint(h['hotel']['name'])
    print('---')
    response = amadeus.next(response)
    for h in response.data:
        ppr.pprint(h['hotel']['name'])

except ResponseError as error:
    print(error)