"""RULE-015/020 universe. ETFs are monitoring proxies, never FT purchase orders.

Breadth is an explicitly labelled, fixed three-company sample per sector, not
the percentage of all index constituents. No later winners enter a replay.
"""
SECTORS = {
    "XLK": ("Information technology", ("MSFT", "NVDA", "AVGO")),
    "XLC": ("Communication services", ("META", "GOOGL", "NFLX")),
    "XLY": ("Consumer discretionary", ("AMZN", "TSLA", "HD")),
    "XLP": ("Consumer staples", ("WMT", "COST", "PG")),
    "XLE": ("Energy", ("XOM", "CVX", "EOG")),
    "XLF": ("Financials", ("JPM", "BAC", "BRK-B")),
    "XLV": ("Healthcare", ("LLY", "AMGN", "JNJ")),
    "XLI": ("Industrials", ("CAT", "GE", "RTX")),
    "XLB": ("Materials", ("LIN", "FCX", "NEM")),
    "XLU": ("Utilities", ("NEE", "SO", "DUK")),
    "XLRE": ("Real estate", ("PLD", "AMT", "EQIX")),
}
INDUSTRIES = {
    "QQQ": "Nasdaq growth", "SOXX": "Semiconductors", "IGV": "Software",
    "XBI": "Biotechnology", "CRAK": "Refining", "XOP": "Oil exploration",
    "FCG": "Natural gas", "OIH": "Oil services", "AMLP": "Midstream",
    "KBE": "Banks", "KRE": "Regional banks", "KIE": "Insurance",
    "XAR": "Aerospace and defence", "XRT": "Retail", "COPX": "Copper",
    "GDX": "Gold miners", "XME": "Metals and mining", "ITB": "Homebuilding",
    "IYT": "Transportation", "TAN": "Solar", "URA": "Uranium",
    "RSP": "Equal-weight S&P 500",
}
ETF_SYMBOLS = set(SECTORS) | set(INDUSTRIES) | {"SPY", "EFA", "ITA"}
COMPANY_SECTOR = {t: s for s, (_, members) in SECTORS.items() for t in members}
COMPANY_SECTOR.update({
    **dict.fromkeys(("ALAB", "CIEN", "MU", "ANET", "CRWV", "NBIS", "CRM", "NOW", "DDOG", "PLTR", "PANW", "CRWD"), "XLK"),
    **dict.fromkeys(("GEV", "PWR", "ETN", "VRT", "LMT", "AVAV", "RKLB", "UBER"), "XLI"),
    **dict.fromkeys(("FANG", "PSX", "TLW:LSE", "VLO", "MPC", "KMI"), "XLE"),
    "BHP:LSE": "XLB", "VRTX": "XLV", "PGR": "XLF", "NRG": "XLU", "CEG": "XLU",
})
COMPANY_INDUSTRY = {
    **dict.fromkeys(("ALAB", "MU", "NVDA", "AVGO"), "SOXX"),
    **dict.fromkeys(("MSFT", "CRM", "NOW", "DDOG", "PLTR", "PANW", "CRWD"), "IGV"),
    **dict.fromkeys(("CRWV", "NBIS", "CIEN", "ANET"), "QQQ"),
    **dict.fromkeys(("PSX", "MPC", "VLO"), "CRAK"),
    **dict.fromkeys(("FANG", "EOG", "TLW:LSE"), "XOP"),
    "BHP:LSE": "COPX", "FCX": "COPX", "NEM": "GDX", "KMI": "AMLP",
    "PGR": "KIE", "JPM": "KBE", "LMT": "XAR", "RTX": "XAR", "AVAV": "XAR",
}
NASDAQ = {"MSFT", "NVDA", "AVGO", "META", "GOOGL", "NFLX", "AMZN", "TSLA", "COST", "AMGN", "EQIX"}


def extra_symbols():
    result = {t: "NYSEARCA:" + t for t in ETF_SYMBOLS}
    for _, members in SECTORS.values():
        for t in members:
            result[t] = ("NASDAQ:" if t in NASDAQ else "NYSE:") + ("BRK.B" if t == "BRK-B" else t)
    return result
