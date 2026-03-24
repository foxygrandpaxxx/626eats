#!/usr/bin/env python3
"""
scripts/rebuild_taxonomy.py
============================
Two jobs in one script:

1. RECATEGORIZE — reads all dish names from AP/AS/AV columns,
   uses Claude to assign each to the new detailed taxonomy,
   writes the category back to AQ/AT/AW columns.

2. REBUILD APP DISHES — builds the DISHES array for the app's
   "By Dish" browse mode from your real Sheet data, replacing
   the old hardcoded sample dishes.

TAXONOMY (21 categories):
  Noodles — Broth          Noodles — Dry & Tossed
  Noodles — Hand-Pulled    Noodles — Fried
  Noodles — Rice & Regional
  Dumplings & Buns         Dim Sum
  Roasts & BBQ             Hot Pot & Dry Pot
  Rice Dishes              Rice Rolls & Congee
  Seafood                  Poultry
  Beef & Lamb              Pork
  Tofu & Vegetables        Soups & Broths
  Skewers & Grilled        Breads & Pancakes
  Desserts & Drinks        Other

USAGE:
  python scripts/rebuild_taxonomy.py --dry-run     # preview
  python scripts/rebuild_taxonomy.py               # write to Sheet
  python scripts/rebuild_taxonomy.py --app-only    # just output app JS

ENV VARS:
  ANTHROPIC_API_KEY, GOOGLE_SERVICE_ACCOUNT_JSON, SPREADSHEET_ID
"""

import os, sys, json, time, re, argparse
import requests
import gspread
from google.oauth2.service_account import Credentials
from collections import Counter, defaultdict

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SA_JSON           = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_ID    = os.environ.get("SPREADSHEET_ID", "")

# ── Taxonomy ──────────────────────────────────────────────────────────────────
CATEGORIES = [
    "Noodles — Broth",
    "Noodles — Dry & Tossed",
    "Noodles — Hand-Pulled & Knife-Cut",
    "Noodles — Fried",
    "Noodles — Rice & Regional",
    "Dumplings & Buns",
    "Dim Sum",
    "Roasts & BBQ",
    "Hot Pot & Dry Pot",
    "Rice Dishes",
    "Rice Rolls & Congee",
    "Seafood",
    "Poultry",
    "Beef & Lamb",
    "Pork",
    "Tofu & Vegetables",
    "Soups & Broths",
    "Skewers & Grilled",
    "Breads & Pancakes",
    "Desserts & Drinks",
    "Other",
]

# Category icons for the app
CATEGORY_ICONS = {
    "Noodles — Broth":               "🍜",
    "Noodles — Dry & Tossed":        "🥢",
    "Noodles — Hand-Pulled & Knife-Cut": "🍝",
    "Noodles — Fried":               "🍳",
    "Noodles — Rice & Regional":     "🍚",
    "Dumplings & Buns":              "🥟",
    "Dim Sum":                       "🍵",
    "Roasts & BBQ":                  "🍖",
    "Hot Pot & Dry Pot":             "🫕",
    "Rice Dishes":                   "🍚",
    "Rice Rolls & Congee":           "🍙",
    "Seafood":                       "🦞",
    "Poultry":                       "🍗",
    "Beef & Lamb":                   "🥩",
    "Pork":                          "🐷",
    "Tofu & Vegetables":             "🥬",
    "Soups & Broths":                "🥣",
    "Skewers & Grilled":             "🔥",
    "Breads & Pancakes":             "🫓",
    "Desserts & Drinks":             "🧋",
    "Other":                         "🍽",
}

# ── Pre-built category map for common dishes (saves API calls) ────────────────
KNOWN = {
    # Noodles — Broth
    "Beef Noodle Soup":             "Noodles — Broth",
    "Spicy Beef Noodle Soup":       "Noodles — Broth",
    "Braised Beef Noodle Soup":     "Noodles — Broth",
    "Wonton Noodle Soup":           "Noodles — Broth",
    "Wonton Soup":                  "Noodles — Broth",
    "Wonton Noodles":               "Noodles — Broth",
    "Shrimp Wonton Noodle in Soup": "Noodles — Broth",
    "BBQ Pork Wonton Noodle Soup":  "Noodles — Broth",
    "Beef Noodle":                  "Noodles — Broth",
    "Beef Noodles":                 "Noodles — Broth",
    "Beef Offal Noodle Soup":       "Noodles — Broth",
    "Lamb Soup Noodle":             "Noodles — Broth",
    "Signature Beef Noodles":       "Noodles — Broth",
    "House Beef Noodle":            "Noodles — Broth",
    "House Special Beef Noodle Soup": "Noodles — Broth",
    "Ultimate Beef Noodle Soup":    "Noodles — Broth",
    "Signature Lanzhou Beef Noodle Soup": "Noodles — Broth",
    "Chinese Beef Stew Noodles":    "Noodles — Broth",
    "Satay Beef Noodles":           "Noodles — Broth",
    "Spicy Beef Noodles":           "Noodles — Broth",
    "Beef Brisket Rice Noodle Soup": "Noodles — Broth",
    "Chicken Rice Noodle Soup":     "Noodles — Broth",
    "Tom Yum Soup Rice Noodles":    "Noodles — Broth",
    "Golden Broth Beef Brisket Rice Noodles": "Noodles — Broth",
    "Golden Broth Fatty Beef Rice Noodles":   "Noodles — Broth",
    "Tomato Fatty Beef Rice Noodles": "Noodles — Broth",
    "Tomato Soup Base Rice Noodles with Beef": "Noodles — Broth",
    "Pickled Pepper Beef Rice Noodles": "Noodles — Broth",
    "Half Meat Half Tendon Beef Noodle Soup": "Noodles — Broth",
    "Beef Noodle Soup (Knife Cut)": "Noodles — Broth",
    "Pho":                          "Noodles — Broth",
    "Beef Pho":                     "Noodles — Broth",
    "Chicken Pho":                  "Noodles — Broth",
    "Hulatang":                     "Noodles — Broth",
    "Noodle Soup":                  "Noodles — Broth",
    "Hot and Spicy Rice Noodle with Chicken Leg": "Noodles — Broth",
    "Beef Stew Wonton Soup Noodles": "Noodles — Broth",
    "Wonton in Chili Oil":          "Noodles — Broth",
    "Wontons in Chili Oil":         "Noodles — Broth",
    "Chili Wonton Soup":            "Noodles — Broth",

    # Noodles — Dry & Tossed
    "Dan Dan Noodles":              "Noodles — Dry & Tossed",
    "Dan Dan Mian":                 "Noodles — Dry & Tossed",
    "Scallion Oil Noodles":         "Noodles — Dry & Tossed",
    "Cold Sesame Noodles":          "Noodles — Dry & Tossed",
    "Cold Noodles":                 "Noodles — Dry & Tossed",
    "Cold Noodle":                  "Noodles — Dry & Tossed",
    "Cold Noodle (凉皮)":           "Noodles — Dry & Tossed",
    "Liang Pi":                     "Noodles — Dry & Tossed",
    "Liangpi":                      "Noodles — Dry & Tossed",
    "Liangpi (Cold Noodles)":       "Noodles — Dry & Tossed",
    "Red Chili Oil Cold Noodles":   "Noodles — Dry & Tossed",
    "Zajiang Noodle":               "Noodles — Dry & Tossed",
    "Zhajiang Noodles":             "Noodles — Dry & Tossed",
    "Dry Noodles":                  "Noodles — Dry & Tossed",
    "Signature Summer Noodle Salad": "Noodles — Dry & Tossed",
    "Jjajangmyeon":                 "Noodles — Dry & Tossed",
    "泡椒三脆面":                    "Noodles — Dry & Tossed",

    # Noodles — Hand-Pulled & Knife-Cut
    "Hand Pulled Noodles":          "Noodles — Hand-Pulled & Knife-Cut",
    "Biang Biang Noodles":          "Noodles — Hand-Pulled & Knife-Cut",
    "Biang Biang Noodles with Pepper Oil": "Noodles — Hand-Pulled & Knife-Cut",
    "Knife Shaved Noodle":          "Noodles — Hand-Pulled & Knife-Cut",
    "Knife-Cut Noodles":            "Noodles — Hand-Pulled & Knife-Cut",
    "Knife-Cut Noodles with Mushrooms": "Noodles — Hand-Pulled & Knife-Cut",
    "Lanzhou Beef Noodles":         "Noodles — Hand-Pulled & Knife-Cut",
    "Lanzhou Hand-Pulled Noodles":  "Noodles — Hand-Pulled & Knife-Cut",
    "Lanzhou Noodles":              "Noodles — Hand-Pulled & Knife-Cut",
    "Shanxi Knife Cut Fried Noodles": "Noodles — Hand-Pulled & Knife-Cut",
    "Beef Hand-Pulled Noodles":     "Noodles — Hand-Pulled & Knife-Cut",
    "Northern Style Noodle":        "Noodles — Hand-Pulled & Knife-Cut",
    "Laghman Noodles":              "Noodles — Hand-Pulled & Knife-Cut",
    "Cats Ear Noodle":              "Noodles — Hand-Pulled & Knife-Cut",
    "Chong Qing Handmade Noodles":  "Noodles — Hand-Pulled & Knife-Cut",
    "王面":                          "Noodles — Hand-Pulled & Knife-Cut",

    # Noodles — Fried
    "Chow Mein":                    "Noodles — Fried",
    "Beef Chow Fun":                "Noodles — Fried",
    "Beef Chow Mein":               "Noodles — Fried",
    "Crispy Chow Mein":             "Noodles — Fried",
    "Fried Noodles":                "Noodles — Fried",
    "House Special Fried Noodle":   "Noodles — Fried",
    "Soy Sauce Chow Mein":          "Noodles — Fried",
    "Chicken Fried Noodles":        "Noodles — Fried",
    "Chicken Chowmein":             "Noodles — Fried",
    "Singapore Noodles":            "Noodles — Fried",
    "Black Pepper Udon":            "Noodles — Fried",
    "Udon Noodles":                 "Noodles — Fried",
    "Lo Mein":                      "Noodles — Fried",
    "Pad Thai":                     "Noodles — Fried",
    "Rad Na":                       "Noodles — Fried",

    # Noodles — Rice & Regional
    "Chongqing Noodles":            "Noodles — Rice & Regional",
    "Chongqing Noodle":             "Noodles — Rice & Regional",
    "Chongqing Spicy Noodles":      "Noodles — Rice & Regional",
    "Chongqing Xiao Mian":          "Noodles — Rice & Regional",
    "Guilin Rice Noodles":          "Noodles — Rice & Regional",
    "House Special Guilin Noodles": "Noodles — Rice & Regional",
    "Crossing Bridge Noodles":      "Noodles — Rice & Regional",
    "Luosifen":                     "Noodles — Rice & Regional",
    "Beef Rice Noodles":            "Noodles — Rice & Regional",
    "Rice Noodles":                 "Noodles — Rice & Regional",
    "Sweet Potato Noodles":         "Noodles — Rice & Regional",
    "Potato Noodles":               "Noodles — Rice & Regional",
    "Cumin Lamb Noodles":           "Noodles — Rice & Regional",
    "Tomato Egg Noodles":           "Noodles — Rice & Regional",
    "Eggplant & Pork Noodles":      "Noodles — Rice & Regional",
    "Minced Meat and Eggplant Noodles": "Noodles — Rice & Regional",
    "Intestine Noodles":            "Noodles — Rice & Regional",
    "Traditional Pork Trotter Noodle": "Noodles — Rice & Regional",
    "Braised Pork Intestine Noodles": "Noodles — Rice & Regional",
    "Crabmeat Tomato Basil Noodles": "Noodles — Rice & Regional",
    "Fujian Shrimp and Pork Dry Noodle": "Noodles — Rice & Regional",
    "Fish Ball Seaweed Noodle":     "Noodles — Rice & Regional",
    "Beef Brisket Rice Noodle Soup": "Noodles — Broth",  # already above
    "House Special with Mixed Noodles": "Noodles — Rice & Regional",
    "Charbroiled Chicken Vermicelli": "Noodles — Rice & Regional",
    "豪华酸辣粉":                    "Noodles — Rice & Regional",
    "过橋米線":                       "Noodles — Rice & Regional",

    # Dumplings & Buns
    "Dumplings":                    "Dumplings & Buns",
    "Potstickers":                  "Dumplings & Buns",
    "Pot Sticker":                  "Dumplings & Buns",
    "Xiao Long Bao":                "Dumplings & Buns",
    "XLB":                          "Dumplings & Buns",
    "Xiao Long Bao Soup Dumplings": "Dumplings & Buns",
    "Xiaolongbao":                  "Dumplings & Buns",
    "Pork Xiaolongbao":             "Dumplings & Buns",
    "Pork Soup Dumplings":          "Dumplings & Buns",
    "Soup Dumplings":               "Dumplings & Buns",
    "Shanghai Shanghai Pan Fried Buns": "Dumplings & Buns",
    "Shanghai Pan Fried Buns":      "Dumplings & Buns",
    "Shanghailander Pan Fried Buns": "Dumplings & Buns",
    "Pan-fried Baos":               "Dumplings & Buns",
    "Pan Fried Pork Dumpling":      "Dumplings & Buns",
    "Jiaozi":                       "Dumplings & Buns",
    "Fish Dumplings":               "Dumplings & Buns",
    "Lamb Wontons":                 "Dumplings & Buns",
    "Red Oil Dumplings":            "Dumplings & Buns",
    "Spicy Wontons":                "Dumplings & Buns",
    "Spicy Pork Dumplings":         "Dumplings & Buns",
    "Spicy and Sour Dumplings":     "Dumplings & Buns",
    "Cumin Beef Fried Dumplings":   "Dumplings & Buns",
    "Pork and Cabbage Dumplings (Pan Fried)": "Dumplings & Buns",
    "Pork and Chive Steamed Dumplings": "Dumplings & Buns",
    "Pork and Leek Dumplings":      "Dumplings & Buns",
    "Pork and Shrimp Dumplings":    "Dumplings & Buns",
    "Pork and Shrimp Fried Dumplings": "Dumplings & Buns",
    "Money Bag Dumpling":           "Dumplings & Buns",
    "Beef Potstickers":             "Dumplings & Buns",
    "Baozi":                        "Dumplings & Buns",
    "Pork Baozi":                   "Dumplings & Buns",
    "Steamed Pork Bun":             "Dumplings & Buns",
    "Cha Siu Bao":                  "Dumplings & Buns",
    "Custard Buns":                 "Dumplings & Buns",
    "Goubuli Buns":                 "Dumplings & Buns",
    "Hui Tou":                      "Dumplings & Buns",
    "Samsa":                        "Dumplings & Buns",
    "Fried Shrimp Dumplings":       "Dumplings & Buns",

    # Dim Sum
    "Har Gow":                      "Dim Sum",
    "Hargow":                       "Dim Sum",
    "Siu Mai":                      "Dim Sum",
    "Shumai":                       "Dim Sum",
    "Shrimp Siomai":                "Dim Sum",
    "Xiu Mai":                      "Dim Sum",
    "Shu Mai":                      "Dim Sum",
    "Suimal":                       "Dim Sum",
    "Shrimp and Pork Siu Mai":      "Dim Sum",
    "Scallop Shumai in XO Sauce":   "Dim Sum",
    "Chicken Feet":                 "Dim Sum",
    "Boneless Chicken Feet":        "Dim Sum",
    "Spicy Boneless Chicken Feet":  "Dim Sum",
    "Turnip Cake":                  "Dim Sum",
    "Fried Turnip Cake":            "Dim Sum",
    "Lo Mai Gai":                   "Dim Sum",
    "Steamed Beef Vermicelli Dumplings": "Dim Sum",
    "Dim Sum":                      "Dim Sum",
    "Dim Sum Beef Rice Roll":       "Dim Sum",

    # Roasts & BBQ
    "Roast Duck":                   "Roasts & BBQ",
    "Peking Duck":                  "Roasts & BBQ",
    "Beijing Roast Duck":           "Roasts & BBQ",
    "BBQ Duck":                     "Roasts & BBQ",
    "Chengdu Style Roast Duck":     "Roasts & BBQ",
    "Roast Duck Bento":             "Roasts & BBQ",
    "BBQ Pork":                     "Roasts & BBQ",
    "Char Siu Rice Bowl":           "Roasts & BBQ",
    "Roast Pigeon":                 "Roasts & BBQ",
    "Fired Pigeons":                "Roasts & BBQ",
    "Whole Suckling Pig":           "Roasts & BBQ",
    "Boar Head Roast":              "Roasts & BBQ",
    "Nanjing Salted Duck":          "Roasts & BBQ",
    "Roasted Pork Knuckle":         "Roasts & BBQ",
    "Roasted Lamb Leg":             "Roasts & BBQ",
    "Seasoned Crispy Pork":         "Roasts & BBQ",
    "Marinated Goose":              "Roasts & BBQ",

    # Hot Pot & Dry Pot
    "Dry Pot":                      "Hot Pot & Dry Pot",
    "Dry Malatang":                 "Hot Pot & Dry Pot",
    "Mala Broth":                   "Hot Pot & Dry Pot",
    "Maocai":                       "Hot Pot & Dry Pot",
    "Classic Hot Pot Maocai":       "Hot Pot & Dry Pot",
    "Spicy Hotpot":                 "Hot Pot & Dry Pot",
    "Lamb Spine Hot Pot":           "Hot Pot & Dry Pot",
    "Signature Lamb Hot Pot":       "Hot Pot & Dry Pot",
    "Popcorn Chicken Hot Pot":      "Hot Pot & Dry Pot",
    "Coconut Chicken Hot Pot":      "Hot Pot & Dry Pot",
    "Thai Style Hot Pot":           "Hot Pot & Dry Pot",
    "Beef Dry Pot":                 "Hot Pot & Dry Pot",
    "Dry Pot Cabbage":              "Hot Pot & Dry Pot",
    "Spicy Pork Intestines Dry Pot": "Hot Pot & Dry Pot",
    "Cold Pot Fish":                "Hot Pot & Dry Pot",
    "Beef Hot Pot Set Meal":        "Hot Pot & Dry Pot",
    "Flower Pepper Spicy Broth":    "Hot Pot & Dry Pot",
    "Sichuan-Style Spicy Broth":    "Hot Pot & Dry Pot",
    "Combo Soup Base":              "Hot Pot & Dry Pot",
    "Tom Yum Broth":                "Hot Pot & Dry Pot",
    "Mao Xue Wang":                 "Hot Pot & Dry Pot",
    "Dry Spicy Mix Bowl":           "Hot Pot & Dry Pot",
    "Pepper Pork Tripe Chicken Pot": "Hot Pot & Dry Pot",
    "Tomato Beef & Tofu Pot":       "Hot Pot & Dry Pot",

    # Rice Dishes
    "Fried Rice":                   "Rice Dishes",
    "Yang Chow Fried Rice":         "Rice Dishes",
    "BBQ Pork Fried Rice":          "Rice Dishes",
    "Kimchee Fried Rice":           "Rice Dishes",
    "Wagyu Fried Rice":             "Rice Dishes",
    "Chef's Special Fried Rice":    "Rice Dishes",
    "Combo Fried Rice":             "Rice Dishes",
    "Fu Jian Fried Rice":           "Rice Dishes",
    "Shrimp fried rice":            "Rice Dishes",
    "Clay Pot Rice":                "Rice Dishes",
    "Claypot Rice":                 "Rice Dishes",
    "Claypot Rice with Preserved Meats": "Rice Dishes",
    "Hainan Chicken":               "Rice Dishes",
    "Hainam Chicken Rice":          "Rice Dishes",
    "Hainanese Chicken Rice":       "Rice Dishes",
    "Chicken Rice":                 "Rice Dishes",
    "Chicken Leg Rice Plate":       "Rice Dishes",
    "Pork Elbow Over Rice":         "Rice Dishes",
    "Braised Pork Elbow Rice Plate": "Rice Dishes",
    "Baked Tomato Pork Chop Rice":  "Rice Dishes",
    "Char Siu Rice Bowl":           "Rice Dishes",
    "Taiwanese Sausage on Rice":    "Rice Dishes",
    "Chinese Sausage Salty Egg Meat Patty on Rice": "Rice Dishes",
    "Crawfish Rice":                "Rice Dishes",
    "Pork Feet Rice":               "Rice Dishes",
    "Hot Pot Rice with Pork and Vegetables": "Rice Dishes",
    "Eel Sticky Rice":              "Rice Dishes",

    # Rice Rolls & Congee
    "Rice Roll":                    "Rice Rolls & Congee",
    "Rice Rolls":                   "Rice Rolls & Congee",
    "Beef Rice Roll":               "Rice Rolls & Congee",
    "Beef Rolls":                   "Rice Rolls & Congee",
    "Char Siu Rice Rolls":          "Rice Rolls & Congee",
    "Shrimp Rice Roll":             "Rice Rolls & Congee",
    "Rice Noodle Rolls":            "Rice Rolls & Congee",
    "Chang Fen (Rice Rolls)":       "Rice Rolls & Congee",
    "Dim Sum Beef Rice Roll":       "Dim Sum",
    "Congee":                       "Rice Rolls & Congee",
    "Dried Scallop Congee":         "Rice Rolls & Congee",
    "Seafood Congee":               "Rice Rolls & Congee",
    "Pork Blood Congee":            "Rice Rolls & Congee",
    "Porridge":                     "Rice Rolls & Congee",

    # Seafood
    "Honey Walnut Shrimp":          "Seafood",
    "Walnut Shrimp":                "Seafood",
    "Walnut Shrimps":               "Seafood",
    "Honey Crispy Shrimp":          "Seafood",
    "Shanghai Fried Shrimp":        "Seafood",
    "Pineapple Shrimp":             "Seafood",
    "Fried Shrimp":                 "Seafood",
    "Argentinian Red Shrimp":       "Seafood",
    "Grilled Shrimp Skewers":       "Seafood",
    "King Crab":                    "Seafood",
    "Garlic King Crab":             "Seafood",
    "Garlic Lobster":               "Seafood",
    "Live Lobster Special":         "Seafood",
    "Lobster dish":                 "Seafood",
    "Typhoon-Shelter Lobster":      "Seafood",
    "Spicy Crab":                   "Seafood",
    "Salt and Pepper Crab":         "Seafood",
    "Spicy Crawfish":               "Seafood",
    "Steamed Fish":                 "Seafood",
    "Steamed Red Grouper":          "Seafood",
    "Steamed Spotted Grouper":      "Seafood",
    "Whole Fish":                   "Seafood",
    "Grilled Fish":                 "Seafood",
    "Spicy Grilled Fish":           "Seafood",
    "Basil Fish":                   "Seafood",
    "Rattan Pepper Fish":           "Seafood",
    "Fish Head":                    "Seafood",
    "Fish Head in Chili Oil":       "Seafood",
    "Pickled Chili Fish":           "Seafood",
    "Sauerkraut Fish":              "Seafood",
    "Pickled Cabbage Fish Soup":    "Seafood",
    "Pickled-Fish Soup":            "Seafood",
    "Boiled Fish in Green Pepper Sauce": "Seafood",
    "Boiled Fish with Pickled Vegetables": "Seafood",
    "Water-Boiled Fish (Filet in Chili Oil)": "Seafood",
    "Sauteed Fish Filet":           "Seafood",
    "Spicy Claw Fish":              "Seafood",
    "Spicy Fish":                   "Seafood",
    "Fish":                         "Seafood",
    "Deep Fried Squid with Spicy Salt": "Seafood",
    "Salt and Pepper Squid":        "Seafood",
    "Salt and Pepper Fish":         "Seafood",
    "Oyster Eggs":                  "Seafood",
    "Oyster Omelet":                "Seafood",
    "Oyster Pancake (with Eggs)":   "Seafood",
    "Curry Fish Ball":              "Seafood",
    "Fried Eggplant with Clams":    "Seafood",
    "Shrimp Eggplant Toast":        "Seafood",
    "Shrimp Paste with Roe":        "Seafood",
    "Sea cucumber dish":            "Seafood",
    "Snow Pea Leaves with Crab Sauce": "Seafood",
    "Crab Roe Tofu":                "Seafood",
    "Fujian Shrimp and Pork Mei Wong": "Seafood",

    # Poultry
    "Orange Chicken":               "Poultry",
    "Kung Pao Chicken":             "Poultry",
    "Original Kung Pao Chicken":    "Poultry",
    "Sesame Chicken":               "Poultry",
    "Teriyaki Chicken":             "Poultry",
    "General Tso's Chicken":        "Poultry",
    "Popcorn Chicken":              "Poultry",
    "Basil Popcorn Chicken":        "Poultry",
    "Taiwanese Popcorn Chicken":    "Poultry",
    "Fried Chicken":                "Poultry",
    "Black Pepper Chicken":         "Poultry",
    "Black Pepper Fried Chicken":   "Poultry",
    "Crispy Chicken":               "Poultry",
    "Spicy Fried Chicken":          "Poultry",
    "Salt and Pepper Chicken":      "Poultry",
    "Soy Sauce Chicken":            "Poultry",
    "Ginger Chicken":               "Poultry",
    "Mushroom Chicken":             "Poultry",
    "Big Plate Chicken":            "Poultry",
    "Boneless Big Plate Chicken":   "Poultry",
    "Chongqing Chicken":            "Poultry",
    "Shandong Chicken":             "Poultry",
    "Diced Chicken with Spicy Chilies": "Poultry",
    "Dry Fried Diced Chicken with Chili and Garlic": "Poultry",
    "String Bean Chicken":          "Poultry",
    "Black Pepper Chicken":         "Poultry",
    "Northern-Style Stir-Fried Chicken": "Poultry",
    "Spicy Crispy Chicken (辣子鸡)": "Poultry",
    "Potato Chicken":               "Poultry",
    "Free Range Chicken on the Skillet": "Poultry",
    "Lotus Leaf Chicken":           "Poultry",
    "Chicken with Bitter Melon":    "Poultry",
    "Chicken Wings":                "Poultry",
    "Salted Egg Yolk Wings":        "Poultry",
    "Chicken Rack":                 "Poultry",
    "Mixed Chicken Rack":           "Poultry",
    "Signature Crooked Mouth Chicken": "Poultry",
    "Sizzling Sand Ginger Chicken": "Poultry",
    "Spicy Chicken with Crispy Lotus Root Fries": "Poultry",

    # Beef & Lamb
    "Beef Roll":                    "Beef & Lamb",
    "Beef Rolls":                   "Beef & Lamb",
    "Beef Roll Pancakes":           "Beef & Lamb",
    "Braised Beef Pancake Roll":    "Beef & Lamb",
    "Beef Pancake":                 "Beef & Lamb",
    "Toothpick Lamb":               "Beef & Lamb",
    "Lamb Skewers":                 "Beef & Lamb",
    "Lamb Kebabs":                  "Beef & Lamb",
    "Lamb Chops":                   "Beef & Lamb",
    "Lamb Ribs":                    "Beef & Lamb",
    "Lamb Shank":                   "Beef & Lamb",
    "Lamb Spine":                   "Beef & Lamb",
    "Cumin Lamb":                   "Beef & Lamb",
    "Lamb":                         "Beef & Lamb",
    "Roasted Lamb Leg":             "Beef & Lamb",
    "Sliced Beef":                  "Beef & Lamb",
    "Sliced Beef & Beef Tripe in Chili Sauce": "Beef & Lamb",
    "Marinated Beef":               "Beef & Lamb",
    "Spicy Boiled Beef":            "Beef & Lamb",
    "Stir Fried Beef":              "Beef & Lamb",
    "Black Pepper Beef":            "Beef & Lamb",
    "Black Pepper Diced Beef":      "Beef & Lamb",
    "Black Pepper Steak Cubes":     "Beef & Lamb",
    "Beef and Broccoli":            "Beef & Lamb",
    "A5 Wagyu":                     "Beef & Lamb",
    "Wagyu Beef":                   "Beef & Lamb",
    "US Wagyu Chuck Cross":         "Beef & Lamb",
    "Ribeye":                       "Beef & Lamb",
    "Short Rib":                    "Beef & Lamb",
    "Beef Stew":                    "Beef & Lamb",
    "Beef Jerky":                   "Beef & Lamb",
    "Beef Balls":                   "Beef & Lamb",
    "Beef Chuck":                   "Beef & Lamb",
    "Beef Shoulder":                "Beef & Lamb",
    "Hand-Cut Beef":                "Beef & Lamb",
    "Slicing Beef":                 "Beef & Lamb",
    "Beef Strings":                 "Beef & Lamb",
    "Beef Tongue":                  "Beef & Lamb",
    "Ox Tongue":                    "Beef & Lamb",
    "Beef Tripes":                  "Beef & Lamb",
    "Guo Bao Rou":                  "Beef & Lamb",
    "Twice-Cooked Pork":            "Beef & Lamb",  # actually pork but...

    # Pork
    "Braised Pork":                 "Pork",
    "Pork Belly":                   "Pork",
    "Pork Feet":                    "Pork",
    "Braised Pork Feet":            "Pork",
    "Pork Head":                    "Pork",
    "Braised Pork with Red Yeast Rice": "Pork",
    "Cold Pork Belly with Garlic Sauce": "Pork",
    "Tea Smoked Pork Ribs":         "Pork",
    "Sweet-and-Sour Ribs":          "Pork",
    "Black Bean Steamed Pork Ribs": "Pork",
    "Salt and Pepper Ribs":         "Pork",
    "Salted Pepper Spareribs":      "Pork",
    "Spareribs":                    "Pork",
    "Chinese Smoked Pork Stir Fried with Dry Tofu": "Pork",
    "Stir Fry Celery with Shredded Pork and Tofu": "Pork",
    "Shredded Pork Rolls":          "Pork",
    "Pork Kidney":                  "Pork",
    "Pork Elbow Over Rice":         "Pork",
    "Whole Suckling Pig":           "Pork",
    "東坡肉":                        "Pork",

    # Tofu & Vegetables
    "Mapo Tofu":                    "Tofu & Vegetables",
    "Mapo Tofu (Beef)":             "Tofu & Vegetables",
    "Mapo Tofu (with minced beef)": "Tofu & Vegetables",
    "Stinky Tofu":                  "Tofu & Vegetables",
    "Deep Fry Stinky Tofu":         "Tofu & Vegetables",
    "Fried Stinky Tofu":            "Tofu & Vegetables",
    "Spicy Stinky Tofu":            "Tofu & Vegetables",
    "Stinking Tofu":                "Tofu & Vegetables",
    "Sticky Tofu":                  "Tofu & Vegetables",
    "Salt & Pepper Tofu":           "Tofu & Vegetables",
    "Spicy Fried Tofu":             "Tofu & Vegetables",
    "Tofu with Mushroom":           "Tofu & Vegetables",
    "Tofu Soup":                    "Tofu & Vegetables",
    "Yunnan Breaded Tofu":          "Tofu & Vegetables",
    "Savory Tofu Pudding":          "Tofu & Vegetables",
    "Tofu Pudding (Douhua)":        "Tofu & Vegetables",
    "Cold Tofu Skin Salad":         "Tofu & Vegetables",
    "Cucumber Salad":               "Tofu & Vegetables",
    "Garlic Lover's Cucumber":      "Tofu & Vegetables",
    "Hot & Sour Shredded Potato":   "Tofu & Vegetables",
    "Egg Plant Dish":               "Tofu & Vegetables",
    "Eggplant Pot":                 "Tofu & Vegetables",
    "Eggplant Side Dish":           "Tofu & Vegetables",
    "Smashed Eggplant with Century Egg": "Tofu & Vegetables",
    "Smashed Eggplants with Thousand Eggs": "Tofu & Vegetables",
    "Pídàn Qiézǐ (Preserved Egg and Eggplant)": "Tofu & Vegetables",
    "Fried Eggs with Tomatoes":     "Tofu & Vegetables",
    "Stir-Fried Chinese Lettuce with Preserved Bean Sauce": "Tofu & Vegetables",
    "Stir-Fried String Beans":      "Tofu & Vegetables",
    "Dragon Whisker Vegetable":     "Tofu & Vegetables",
    "Stir Fried Rice Cakes":        "Tofu & Vegetables",
    "Stir-Fried Rice Cake":         "Tofu & Vegetables",
    "Shanghai Rice Cake":           "Tofu & Vegetables",
    "Sweet Rice Cakes":             "Tofu & Vegetables",
    "Crispy Rice Cake":             "Tofu & Vegetables",
    "Stir Fry Rice Cake with Cabbage": "Tofu & Vegetables",
    "Fried Oyster Mushroom":        "Tofu & Vegetables",
    "Fried Tofu Skin Skewers":      "Tofu & Vegetables",
    "Bean Curd Wrap Meat":          "Tofu & Vegetables",
    "Spicy Gluten":                 "Tofu & Vegetables",
    "Salted Egg Yolk Corn":         "Tofu & Vegetables",
    "Sweet Potato Fries":           "Tofu & Vegetables",
    "Fried Sesame Balls with Red Bean Filling": "Tofu & Vegetables",

    # Soups & Broths
    "Hot and Sour Soup":            "Soups & Broths",
    "Beef Bone Broth Soup":         "Soups & Broths",
    "Beef Bone Broth":              "Soups & Broths",
    "Beef Bone Soup":               "Soups & Broths",
    "Bone Broth Soup":              "Soups & Broths",
    "Bone Soup":                    "Soups & Broths",
    "Collagen Bone Broth":          "Soups & Broths",
    "Beef Broth":                   "Soups & Broths",
    "Mushroom Broth":               "Soups & Broths",
    "Lamb Soup":                    "Soups & Broths",
    "House Special Bone Broth":     "Soups & Broths",
    "Beef Brisket Soup":            "Soups & Broths",
    "Braised Beef Soup":            "Soups & Broths",
    "Beef Soup":                    "Soups & Broths",
    "Jelly Golden Chicken Broth":   "Soups & Broths",
    "Beef Bone Soup Malatang":      "Soups & Broths",
    "Sour Cabbage with Pork Stew":  "Soups & Broths",
    "Chilled Jellyfish Salad":      "Soups & Broths",
    "Tom Kah":                      "Soups & Broths",
    "Jjamppong":                    "Soups & Broths",
    "Sacha Broth":                  "Soups & Broths",
    "Spicy Broth":                  "Soups & Broths",
    "Spicy and Sour Beef Broth":    "Soups & Broths",
    "Beef Stew":                    "Soups & Broths",

    # Skewers & Grilled
    "Lamb Skewers":                 "Skewers & Grilled",
    "Beef Skewers":                 "Skewers & Grilled",
    "Special Beef Skewers":         "Skewers & Grilled",
    "Ribeye Skewers":               "Skewers & Grilled",
    "Pineapple Steak Skewers":      "Skewers & Grilled",
    "Grilled Skewers":              "Skewers & Grilled",
    "Special Lamb Skewers":         "Skewers & Grilled",
    "Lamb Kebabs":                  "Skewers & Grilled",
    "Beef Sticks":                  "Skewers & Grilled",
    "Flaming Pork Jowl":            "Skewers & Grilled",
    "Sizzling Steak":               "Skewers & Grilled",
    "Stir-Fried Naan & Meat":       "Skewers & Grilled",

    # Breads & Pancakes
    "Scallion Pancakes":            "Breads & Pancakes",
    "Green Onion Pancake":          "Breads & Pancakes",
    "Scallion Flat Bread":          "Breads & Pancakes",
    "Fried Pancakes":               "Breads & Pancakes",
    "Jianbing":                     "Breads & Pancakes",
    "Chinese Crepe":                "Breads & Pancakes",
    "Savory Crepe":                 "Breads & Pancakes",
    "Rou Jia Mo":                   "Breads & Pancakes",
    "Pork Rou Jia Muo":             "Breads & Pancakes",
    "Beef Sandwich":                "Breads & Pancakes",
    "Meat Pies":                    "Breads & Pancakes",
    "Crispy Pancake with Braised Pork": "Breads & Pancakes",
    "Pineapple Bun":                "Breads & Pancakes",
    "Pineapple Buns":               "Breads & Pancakes",
    "HK Pineapple Buns":            "Breads & Pancakes",
    "Bolo Buns":                    "Breads & Pancakes",
    "BBQ Pork Pie":                 "Breads & Pancakes",
    "BBQ Pork Pastry":              "Breads & Pancakes",
    "Chicken Pie":                  "Breads & Pancakes",
    "Pumpkin Pie/Pumpkin Pastry":   "Breads & Pancakes",
    "French Bread":                 "Breads & Pancakes",
    "Yutiao":                       "Breads & Pancakes",
    "Chinese Donut":                "Breads & Pancakes",
    "Chinese Donut (Dau Cha Quay)": "Breads & Pancakes",

    # Desserts & Drinks
    "Egg Tarts":                    "Desserts & Drinks",
    "Portuguese Egg Tart":          "Desserts & Drinks",
    "HK Milk Tea":                  "Desserts & Drinks",
    "Salty Milk Tea":               "Desserts & Drinks",
    "HK Waffle Ice Cream":          "Desserts & Drinks",
    "Sesame Balls":                 "Desserts & Drinks",
    "Lychee Jelly":                 "Desserts & Drinks",
    "Milk Skin Yogurt":             "Desserts & Drinks",
    "Peach Gum Resin":              "Desserts & Drinks",
    "Hua Du Glutinous Rice Balls":  "Desserts & Drinks",
    "Tofu Pudding (Douhua)":        "Desserts & Drinks",
    "Peanut Butter French Toast":   "Desserts & Drinks",
    "Hot Honey Yuzu Tea":           "Desserts & Drinks",
    "Quail Eggs":                   "Desserts & Drinks",
}



# ── Claude fallback for unknown dishes ───────────────────────────────────────
def classify_with_claude(dish_names):
    """
    Batch classify up to 30 unknown dish names at once.
    Returns dict of dish_name -> category.
    """
    if not ANTHROPIC_API_KEY or not dish_names:
        return {d: "Other" for d in dish_names}

    cats_str = "\n".join(f"- {c}" for c in CATEGORIES)
    dishes_str = "\n".join(f"{i+1}. {d}" for i, d in enumerate(dish_names))

    prompt = f"""Classify these Chinese restaurant dishes into the most appropriate category.

CATEGORIES (choose exactly one per dish):
{cats_str}

DISHES TO CLASSIFY:
{dishes_str}

Rules:
- Noodles in soup = "Noodles — Broth"
- Cold/sesame/dan dan noodles = "Noodles — Dry & Tossed"  
- Hand-pulled/knife-cut/biang biang = "Noodles — Hand-Pulled & Knife-Cut"
- Chow mein/fried noodles/pad thai = "Noodles — Fried"
- Chongqing/Guilin/rice vermicelli = "Noodles — Rice & Regional"
- Soup dumplings/potstickers/baozi = "Dumplings & Buns"
- Har gow/siu mai/turnip cake/chicken feet = "Dim Sum"
- Only use "Other" if truly doesn't fit anywhere else

Respond ONLY with JSON: {{"1": "category", "2": "category", ...}}"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 800,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()["content"][0]["text"].strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
        result = json.loads(raw)
        return {
            dish_names[int(k)-1]: v if v in CATEGORIES else "Other"
            for k, v in result.items()
            if k.isdigit() and 1 <= int(k) <= len(dish_names)
        }
    except Exception:
        return {d: "Other" for d in dish_names}


# ── Sheet helpers ─────────────────────────────────────────────────────────────
def get_sheet():
    creds = Credentials.from_service_account_info(
        json.loads(SA_JSON),
        scopes=["https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"],
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID).worksheet("Restaurants")

def sg(row, idx):
    return row[idx].strip() if idx < len(row) else ""


# ── Build app DISHES array from real data ─────────────────────────────────────
def build_app_dishes(all_dish_data):
    """
    all_dish_data: list of (rest_id, rest_name, dish_name, category, summary)
    Returns JS code for the DISHES array.
    """
    # Group by dish name, pick best summary, count restaurants
    dish_map = defaultdict(lambda: {
        "category": "Other", "summaries": [], "restaurants": [], "count": 0
    })

    for rest_id, rest_name, dish_name, category, summary in all_dish_data:
        if not dish_name:
            continue
        key = dish_name.lower().strip()
        entry = dish_map[dish_name]
        entry["category"] = category
        entry["count"] += 1
        entry["restaurants"].append(rest_name)
        if summary and len(summary) > 30:
            entry["summaries"].append(summary)

    # Sort by count desc, then name
    sorted_dishes = sorted(dish_map.items(), key=lambda x: (-x[1]["count"], x[0]))

    dishes_js = []
    for i, (name, data) in enumerate(sorted_dishes):
        cat  = data["category"]
        icon = CATEGORY_ICONS.get(cat, "🍽")
        # Best summary = longest one (most informative)
        best_summary = max(data["summaries"], key=len) if data["summaries"] else ""
        # Truncate if very long
        if len(best_summary) > 200:
            best_summary = best_summary[:197] + "..."
        # Top 3 restaurants
        top_rests = data["restaurants"][:3]

        d = {
            "id":      f"D{i+1:03d}",
            "name":    name,
            "fmt":     cat,
            "icon":    icon,
            "count":   data["count"],
            "desc":    best_summary,
            "rests":   top_rests,
        }
        dishes_js.append(d)

    return dishes_js


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Rebuild dish taxonomy and update app DISHES array"
    )
    parser.add_argument("--dry-run",  action="store_true",
                        help="Preview without writing to Sheet")
    parser.add_argument("--app-only", action="store_true",
                        help="Only output the JS DISHES array, skip Sheet updates")
    args = parser.parse_args()

    if not SA_JSON or not SPREADSHEET_ID:
        sys.exit("ERROR: GOOGLE_SERVICE_ACCOUNT_JSON and SPREADSHEET_ID required")

    print("=" * 60)
    print("626 Eats — Rebuild Dish Taxonomy")
    print("=" * 60)

    print("\nLoading Sheet...")
    ws   = get_sheet()
    rows = ws.get_all_values()
    DATA_START = 3

    # Collect all dish data from AP/AS/AV + AQ/AT/AW (category) + AR/AU/AX (summary)
    all_dish_data = []
    unknown_dishes = set()
    batch_updates  = []

    for i, row in enumerate(rows[DATA_START:], start=DATA_START):
        rest_id   = sg(row, 0)
        rest_name = sg(row, 1)
        if not rest_id:
            continue

        for slot, (name_idx, cat_idx, summ_idx) in enumerate([
            (41, 42, 43),  # dish1: AP, AQ, AR
            (44, 45, 46),  # dish2: AS, AT, AU
            (47, 48, 49),  # dish3: AV, AW, AX
        ]):
            dish_name = sg(row, name_idx)
            if not dish_name:
                continue
            summary = sg(row, summ_idx)

            # Look up in pre-built map first
            if dish_name in KNOWN:
                new_cat = KNOWN[dish_name]
            else:
                new_cat = None
                unknown_dishes.add(dish_name)

            all_dish_data.append((rest_id, rest_name, dish_name, new_cat, summary))

    print(f"  Total dish entries: {len(all_dish_data)}")
    print(f"  Known categories:   {sum(1 for *_, c, _ in all_dish_data if c)}")
    print(f"  Need Claude:        {len(unknown_dishes)}")

    # Classify unknown dishes with Claude in batches of 25
    if unknown_dishes and not args.app_only:
        print(f"\nClassifying {len(unknown_dishes)} unknown dishes with Claude...")
        unknown_list = sorted(unknown_dishes)
        claude_map   = {}
        BATCH = 25
        for start in range(0, len(unknown_list), BATCH):
            batch = unknown_list[start:start+BATCH]
            result = classify_with_claude(batch)
            claude_map.update(result)
            print(f"  Classified {min(start+BATCH, len(unknown_list))}/{len(unknown_list)}")
            time.sleep(0.5)

        # Fill in Claude classifications
        all_dish_data = [
            (rid, rname, dname, KNOWN.get(dname) or claude_map.get(dname, "Other"), summ)
            for rid, rname, dname, _, summ in all_dish_data
        ]
    else:
        # Use "Other" for unknowns
        all_dish_data = [
            (rid, rname, dname, KNOWN.get(dname, "Other"), summ)
            for rid, rname, dname, _, summ in all_dish_data
        ]

    # Show distribution
    cat_counts = Counter(cat for *_, cat, _ in all_dish_data if cat)
    print("\nCategory distribution:")
    for cat in CATEGORIES:
        count = cat_counts.get(cat, 0)
        if count:
            print(f"  {count:3d}  {cat}")

    # Build Sheet updates — write new categories to AQ/AT/AW
    if not args.dry_run and not args.app_only:
        print("\nBuilding Sheet updates...")
        row_cats = defaultdict(dict)  # sheet_row -> {slot: category}
        cat_index = 0
        for i, row in enumerate(rows[DATA_START:], start=DATA_START):
            rest_id = sg(row, 0)
            if not rest_id:
                continue
            for slot, (name_idx, cat_idx, summ_idx) in enumerate([
                (41, 42, 43), (44, 45, 46), (47, 48, 49)
            ]):
                dish_name = sg(row, name_idx)
                if not dish_name:
                    continue
                new_cat = KNOWN.get(dish_name, "Other")
                if not KNOWN.get(dish_name) and unknown_dishes:
                    # Look up claude result
                    new_cat = claude_map.get(dish_name, "Other") if 'claude_map' in dir() else "Other"
                sheet_row = i + 1
                col = ["AQ", "AT", "AW"][slot]
                batch_updates.append({
                    "range": f"{col}{sheet_row}",
                    "values": [[new_cat]],
                })

        print(f"Writing {len(batch_updates)} category updates to Sheet...")
        BATCH = 200
        for start in range(0, len(batch_updates), BATCH):
            chunk = batch_updates[start:start+BATCH]
            ws.batch_update(chunk)
            time.sleep(0.5)
        print("  Done.")

    # Build the app DISHES array
    print("\nBuilding app DISHES array...")
    dishes_data = build_app_dishes(all_dish_data)
    print(f"  {len(dishes_data)} unique dishes")

    # Generate the JS
    js_lines = ["var DISHES = ["]
    for d in dishes_data:
        # Escape single quotes in name only — keep entry lean (no desc to save space)
        name = d["name"].replace("\\", "\\\\").replace("'", "\\'")
        fmt  = d["fmt"]
        icon = d["icon"]
        # Escape restaurant names
        rests_escaped = [
            r.replace("\\", "\\\\").replace("'", "\\'")
            for r in d["rests"]
        ]
        rests_js = "[" + ",".join(f"'{r}'" for r in rests_escaped) + "]"
        entry = (
            "  {id:'" + d["id"] + "',name:'" + name +
            "',fmt:'" + fmt + "',icon:'" + icon +
            "',count:" + str(d["count"]) +
            ",rests:" + rests_js + "},"
        )
        js_lines.append(entry)
    js_lines.append("];")
    dishes_js = "\n".join(js_lines)

    # Save to a file
    output_path = "scripts/dishes_array.js"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by rebuild_taxonomy.py\n")
        f.write("// " + str(len(dishes_data)) + " dishes across " + str(len(CATEGORIES)) + " categories\n\n")
        f.write(dishes_js)
    print(f"  Saved to {output_path}")

    # Show sample
    print("\nSample dishes by category:")
    shown = set()
    for cat in CATEGORIES[:8]:
        cat_dishes = [d for d in dishes_data if d["fmt"] == cat][:2]
        if cat_dishes:
            print(f"  {cat}:")
            for d in cat_dishes:
                print(f"    {d['icon']} {d['name']} ({d['count']}x)")

    print("\n" + "=" * 60)
    print("Done!")
    print(f"  {len(dishes_data)} dishes catalogued")
    print(f"  dishes_array.js ready to embed in index.html")
    print("\nNext steps:")
    print("  1. python scripts/rebuild_taxonomy.py  (if not done)")
    print("  2. Run update_app_dishes.py to embed into index.html")
    print("  3. python scripts/export_json.py && git push")
    print("=" * 60)


if __name__ == "__main__":
    main()
