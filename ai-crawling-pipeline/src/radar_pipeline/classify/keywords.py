"""Classification keyword lists — verbatim port of codigo.gs constants.

These are the PT-BR matching tables used by classify/rules.py.
"""

# Priority-ordered keyword table: first match wins.
# Each entry is (event_type, alert_level, [keywords...])
TEMAS_KW = [
    ("Evento", "Baixo", [
        "palestra", "curso ", "workshop", "webinar", "treinamento",
        "capacitação", "capacitacao", "seminário", "seminario",
        "painel sobre",
    ]),
    ("Institucional", "Baixo", [
        "aniversário", "aniversario", "prêmio", "premio", "patrocínio",
        "patrocinio", "certificação", "certificacao", "doação", "doacao",
        "campanha social",
    ]),
    ("M&A", "Alto", [
        "aquisição", "aquisicao", "fusão", "fusao", "adquire",
        "compra da", "incorpora", "chapter 11", "recuperação judicial",
        "recuperacao judicial", "venda da divisão", "venda da divisao",
        "joint venture",
    ]),
    ("Preco", "Alto", [
        "reajuste", "aumento de preço", "aumento de preco",
        "tabela de preço", "tabela de preco", "corte de preço",
        "corte de preco",
    ]),
    ("Investimento", "Alto", [
        "nova fábrica", "nova fabrica", "nova planta", "investe r$",
        "investimento de r$", "amplia produção", "amplia producao",
    ]),
    ("Lancamento", "Medio", [
        "lança", "lanca", "lançamento", "nova linha", "novo produto",
        "amplia portfólio", "amplia portfolio", "amplia linha",
    ]),
    ("Distribuicao", "Medio", [
        "distribuidor", "distribuição", "distribuicao",
        "centro de distribuição", "centro de distribuicao",
        "novo cd", "logística", "logistica",
    ]),
    ("Investimento", "Medio", [
        "investe", "investimento", "expansão", "expansao",
    ]),
    ("Executivos", "Medio", [
        "nomeia", "assume a diretoria", "novo ceo", "novo presidente",
        "nova diretoria",
    ]),
    ("Indicador", "Medio", [
        "ipca", "selic", "copom", "pib", "câmbio", "cambio",
        "dólar", "dolar", "emplacamento", "produção de veículos",
        "producao de veiculos",
    ]),
    ("Evento", "Baixo", [
        "automec", "feira", "congresso", "evento",
    ]),
]

# Out-of-scope blocklist — articles matching any of these are filtered out.
FORA_ESCOPO = [
    # Local politics
    "prefeitura", "prefeito", "vereador", "câmara municipal",
    "camara municipal", "deputado", "senador",
    # Religion
    "missa", "paróquia", "paroquia", "festa junina",
    # Sports
    "campeonato", "futebol", "vôlei", "volei", "basquete",
    "copa do mundo", "sub-20", "sub-17",
    # Crime
    "homicídio", "homicidio", "assassinato", "polícia prende",
    "policia prende", "preso por",
    # Education / misc
    "escola municipal", "escola estadual", "vestibular", "enem",
    "loteria", "horóscopo", "horoscopo", "novela", "bbb ",
    "pista de caminhada", "parque no vale",
    # Food / beverage
    "sabor", "refrigerante", "refri ", "cerveja", "nutella", "oreo",
    "biscoito", "wafer", "pipoca", "paçoquita", "pacoquita", "whey",
    "guaraná", "guarana", "azeite", "limonada", "hambúrguer",
    "hamburguer", "sorvete", "gelo para drink", "snack", "salgadinho",
    "churrasco", "maçã do amor", "maca do amor", "delivery de",
    "espetinho", "merenda", "starbucks", "burger king", "red bull",
    "pepsi", "coca-cola", "chupa-chupa", "chupa chups", "kebab",
    "nestlé", "nestle", "ninho cremosinho",
    # Entertainment
    "filme", "trailer", "cinema", "série de tv", "temporada de",
    "netflix", "prime video", "disney+", "marilyn monroe",
    "diabo veste prada", "gta ", "forza horizon", "videogame",
    "video game", "música inédita", "álbum", "disco novo",
    "banda ", "heavy metal", "anthrax", "turnê", "turne",
    "eurovisão", "eurovisao", "festival de música",
    "festival de musica", "show de",
    # Fashion / beauty
    "maquiagem", "batom", "sombra", "delineador", "coleção cápsula",
    "colecao capsula", "moda sustentável", "moda sustentavel",
    "vestido", "primark", "desfile", "passarela", "fashion week",
    "joia favorita", "bijuteria",
    # Fighting sports
    "ufc", "mma", "boxe", "luta livre", "wwe", "octógono", "octogono",
    # E-bikes / light mobility
    "bicicleta elétrica", "bicicleta eletrica", "e-bike", "ebike",
    "ebikes", "mountain bike", "mountainbike", "motor de cubo",
    "patinete", "scooter elétric", "scooter eletric", "avinox",
    "garmin", "forerunner", "ciclismo", "pedal", "pedalada",
    "bike ", "bikes ", "bicicleta", "bicicletas", "e bike",
    # Consumer electronics / gadgets
    "celular", "smartphone", "iphone", "android", "xiaomi", "oppo",
    "samsung galaxy", "notebook", "laptop", "kindle", "apple watch",
    "smartwatch", "fone de ouvido", "headphone", "microfone",
    "geladeira", "fogão", "fogao", "steam controller",
    "steam machine", "8bitdo", "arcade", "videogame", "console",
    "playstation", "xbox", "nintendo", "need for speed",
    "electronic arts", "lidl", "gadget", "tablet",
    "câmera de até", "camera de ate", "megapixel", "vaio",
    "snapdragon", "intel core", "armazenamento", "conta google",
    "microsoft", "need for speed",
]

# Automotive aftermarket / replacement parts positive filter.
# An article must contain at least one of these to be classified as a
# "Lancamento / Alto" (product launch) in the classificar_ priority chain.
KW_PECA_AUTOMOTIVA = [
    "autopeça", "autopeças", "autopeca", "reposição", "reposicao",
    "aftermarket", "pós-venda", "pos-venda",
    "peça de reposição", "peca de reposicao", "oficina", "mecânic",
    "mecanic", "reparador", "reparação",
    "freio", "pastilha", "disco de freio", "lona de freio",
    "amortecedor", "suspensão", "suspensao",
    "embreagem", "rolamento", "filtro", "vela de ignição",
    "vela de ignicao", "bobina", "injetor",
    "correia", "tensor", "retentor", "junta", "bronzina",
    "pistão", "pistao", "anel de",
    "palheta", "limpador", "farol", "lanterna", "lâmpada",
    "lampada", "led automotivo", "compressor",
    "radiador", "condensador", "bomba d", "bomba de combustível",
    "bomba de combustivel", "bomba de água", "bomba de agua",
    "terminal de direção", "terminal de direcao", "pivô", "pivo",
    "bandeja", "coxim", "sensor abs",
    "catalisador", "escapamento", "válvula", "valvula",
    "cabeçote", "cabecote", "virabrequim", "comando de válvulas",
    "kit de reparo", "peça", "peças", "componente automotivo",
    "sistema de freio", "sistema de arrefecimento",
    "camisa de cilindro", "camisas de cilindro", "cilindro de motor",
    "biela", "mancal", "turbina", "turbocompressor",
    "fluido de freio", "fluido para freio", "óleo lubrificante",
    "oleo lubrificante", "aditivo", "graxa",
    "correia dentada", "junta homocinética", "junta homocinetica",
    "semi-eixo", "cardã", "carda", "diferencial",
    "cubo de roda", "disco de embreagem", "platô",
    "plato de embreagem", "rolamento de roda", "buchas",
    "batente", "coifa", "homocinética", "homocinetica",
    "bomba de óleo", "bomba de oleo", "válvula termostática",
    "termostato", "ventoinha", "eletroventilador",
    "chicote elétrico", "módulo de injeção", "modulo de injecao",
    "sonda lambda", "sensor de rotação", "sensor de rotacao",
    "atuador", "solenoide", "relé automotivo", "fusível",
    "coxim de motor", "tirante", "barra estabilizadora", "mola",
    "helicoidal", "pinça de freio", "pinca de freio",
    "cilindro mestre", "servo freio", "tambor de freio",
    "cabo de freio", "flexível de freio", "flexivel de freio",
]

# Context anchor — broader sector terms
KW_CONTEXTO_AUTO = [
    "aftermarket", "reposição", "reposicao", "autopeça", "autopeças",
    "autopeca", "veículo", "veiculo", "automotiv", "montadora",
    "frota", "oficina", "distribuidor de peças",
    "distribuidor de pecas",
]

# Launch-verb keywords (higher threshold — used by ehLancamento_)
KW_LANCAMENTO = [
    "lança", "lanca", "lançamento", "lancamento", "nova linha",
    "novo produto", "novos produtos", "nova geração", "nova geracao",
    "amplia portfólio", "amplia portfolio", "amplia linha",
    "amplia a linha", "amplia a oferta", "apresenta linha",
    "apresenta nova", "estreia", "novidade",
    "chega ao mercado", "disponível no mercado", "disponivel no mercado",
    "novo catálogo", "novo catalogo", "novos códigos", "novos codigos",
    "nova versão do", "nova versao do", "passa a oferecer",
    "reformula", "relança", "relanca", "linha inédita", "linha inedita",
    "inaugura linha", "nova fábrica de", "com novos", "novos itens",
    "novos amortecedores", "novas pastilhas", "novos filtros",
    "novas velas", "novos kits", "novos modelos de",
    "amplia a linha", "amplia o portfólio",
    "amplia oferta", "adiciona à linha", "adiciona a linha",
    "incorpora à linha", "novo item",
]
