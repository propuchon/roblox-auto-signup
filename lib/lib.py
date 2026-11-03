"""Local username generation and compatibility account-file writers. No network I/O."""
import random
import secrets
import sys
import os
import json
import tempfile
from pathlib import Path
from datetime import datetime


def getResourcePath(relative_path):
    base_path = getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)
    return str(Path(base_path) / relative_path)


class UsernameGenerator:
    # SOURCE: https://github.com/mrsobakin/pungen. Kudos to @mrsobakin for the original code.
    CONSONANTS = "bcdfghjklmnpqrstvwxyz"
    VOWELS = "aeiou"

    CONS_WEIGHTED = ("tn", "rshd", "lfcm", "gypwb", "vbjxq", "z")
    VOW_WEIGHTED = ("eao", "iu")
    DOUBLE_CONS = ("he", "re", "ti", "ti", "hi", "to", "ll", "tt", "nn", "pp", "th", "nd", "st", "qu")
    DOUBLE_VOW = ("ee", "oo", "ei", "ou", "ai", "ea", "an", "er", "in", "on", "at", "es", "en", "of", "ed", "or", "as")

    def __init__(self, min_length, max_length=None):
        self.set_length(min_length, max_length)

    def set_length(self, min_length, max_length):
        if not max_length:
            max_length = min_length

        self.min_length = min_length
        self.max_length = max_length

    def generate(self):
        username, is_double, num_length = "", False, 0

        if random.randrange(10) > 0:
            is_consonant = True
        else:
            is_consonant = False

        length = random.randrange(self.min_length, self.max_length + 1)

        if random.randrange(5) == 0:
            num_length = random.randrange(3) + 1
            if length - num_length < 2:
                num_length = 0

        letterLength = max(1, length - num_length)
        for j in range(letterLength):
            if len(username) > 0:
                if username[-1] in self.CONSONANTS:
                    is_consonant = False
                elif username[-1] in self.VOWELS:
                    is_consonant = True
            if not is_double:
                if random.randrange(8) == 0 and len(username) < int(letterLength) - 1:
                    is_double = True
                if is_consonant:
                    username += self._get_consonant(is_double)
                else:
                    username += self._get_vowel(is_double)
                is_consonant = not is_consonant
            else:
                is_double = False
        if random.randrange(2) == 0:
            username = username[:1].upper() + username[1:]
        if num_length > 0:
            for j in range(num_length):
                username += str(random.randrange(10))

        return username

    def _get_consonant(self, is_double):
        if is_double:
            return random.choice(self.DOUBLE_CONS)
        else:
            i = random.randrange(100)
            if i < 40:
                weight = 0
            elif 65 > i >= 40:
                weight = 1
            elif 80 > i >= 65:
                weight = 2
            elif 90 > i >= 80:
                weight = 3
            elif 97 > i >= 90:
                weight = 4
            else:
                return self.CONS_WEIGHTED[5]
            return self.CONS_WEIGHTED[weight][random.randrange(len(self.CONS_WEIGHTED[weight]))]

    def _get_vowel(self, is_double):
        if is_double:
            return random.choice(self.DOUBLE_VOW)
        else:
            i = random.randrange(100)
            if i < 70:
                weight = 0
            else:
                weight = 1
            return self.VOW_WEIGHTED[weight][random.randrange(len(self.VOW_WEIGHTED[weight]))]


class Main:
    def usernameCreator(self, nameFormat=None, scrambled=True):
        if nameFormat:
            return nameFormat[:12] + "_" + str(secrets.randbelow(1000000)).zfill(6)
        return self.generateUsername(scrambled=scrambled)

    def generateUsername(self, scrambled=None):
        if scrambled is False:
            try:
                with open(getResourcePath('lib/verbs.txt'), 'r', encoding='utf-8') as f:
                    verbs = f.read().split()
                with open(getResourcePath('lib/nouns.txt'), 'r', encoding='utf-8') as f:
                    nouns = f.read().split()
                with open(getResourcePath('lib/adjectives.txt'), 'r', encoding='utf-8') as f:
                    adjectives = f.read().split()

                if not verbs or not nouns or not adjectives:
                    raise ValueError("One or more word lists are empty")

                verb = random.choice(verbs).strip()
                noun = random.choice(nouns).strip()
                adjective = random.choice(adjectives).strip()
                number = random.randint(10, 99)
                username = (verb + noun + adjective)[:18] + str(number)
                return username
            except FileNotFoundError as e:
                print(f"Warning: Required text files not found: {e}. Falling back to scrambled username.")
                gen = UsernameGenerator(10, 15)
                return gen.generate()
            except Exception as e:
                print(f"Error generating structured username: {e}. Falling back to scrambled username.")
                gen = UsernameGenerator(10, 15)
                return gen.generate()
        else:
            gen = UsernameGenerator(10, 15)
            return gen.generate()

    async def saveAccount(self, account):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open("./cookies.json", "r", encoding="utf-8") as file:
                existingData = json.load(file)
        except FileNotFoundError:
            existingData = []
        if not isinstance(existingData, list):
            raise ValueError("cookies.json must contain an account list; existing data was preserved.")

        accountData = {
            "username": account["username"],
            "password": account["password"],
            "email": account["email"],
            "emailPassword": account["emailPassword"],
            "cookies": account["cookies"]
        }
        existingData.append(accountData)

        # Replace only after the complete new file is written. Read/write errors
        # propagate so the caller cannot report an unsaved account as successful.
        temporaryPath = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=".",
                                             prefix=".cookies-", suffix=".tmp", delete=False) as jsonFile:
                temporaryPath = jsonFile.name
                json.dump(existingData, jsonFile, indent=4)
                jsonFile.flush()
                os.fsync(jsonFile.fileno())
            os.replace(temporaryPath, "./cookies.json")
        finally:
            if temporaryPath and os.path.exists(temporaryPath):
                os.remove(temporaryPath)

        with open("./accounts.txt", "a", encoding="utf-8") as f:
            f.write(
                f"Username: {account['username']}, Password: {account['password']}, Email: {account['email']}, Email Password: {account['emailPassword']} (Created at {timestamp})\n"
            )

    async def saveTiming(self, username, status, seconds, stages):
        try:
            with open("./timings.csv", "a", encoding="utf-8") as f:
                f.write(f"{username},{status},{seconds:.1f},{'; '.join(stages)}\n")
        except Exception as e:
            print(f"Error writing to timings.csv: {e}")
