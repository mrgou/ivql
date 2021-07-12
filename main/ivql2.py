import pandas as pd
import tabulate
import requests
import json
import csv
import argparse
import time
import os
import configparser
import sqlite3
import sys

from dataclasses import dataclass
from http.client import responses
from re import match
from urllib.parse import urlparse
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from tabulate import tabulate


class AuthenticationException(Exception):
    pass


class HttpException(Exception):
    pass


@dataclass
class session_details:
    sessionId: str
    mainvault: tuple
    allvaults: dict


class custom_df(pd.DataFrame):
    def __init__(self, *args):
        pd.DataFrame.__init__(self, *args)

    def expand(self):
        def expand_col(col, sep="_"):
            df = col.apply(pd.Series)
            if 0 in df.columns:  # this occurs for NaN rows
                df.drop(columns=0, inplace=True)
            mapping = {newcol: f"{col.name}{sep}{newcol}" for newcol in df.columns}
            df.rename(mapping, axis="columns", inplace=True)
            return df

        while True:
            processed = False
            for col in self.columns:
                first_val = self[col].first_valid_index()
                if first_val != None:
                    if type(self[col].iloc[first_val]) == list:
                        self = self.explode(col)
                        processed = True
            self = self.reset_index(drop=True)
            for col in self.columns:
                first_val = self[col].first_valid_index()
                if first_val != None:
                    if type(self[col].iloc[first_val]) == dict:
                        self = pd.concat(
                            [self, expand_col(self[col])],
                            axis="columns",
                        ).drop(col, axis="columns")
                        processed = True
            if not processed:
                break
        return self

    @staticmethod
    def cjson_normalize(data):
        return custom_df(pd.json_normalize(data))


def authorize(vault: str, user_name: str, password: str) -> session_details:
    """
    Authenticates in the specified Vault and returns a session
    details object.
    In case authentication fails, raises a custom exception
    """
    try:

        param = {"username": user_name, "password": password}
        url = "https://" + vault + ".veevavault.com/api/v20.3/auth"
        auth = requests.post(url, params=param)
        if auth.status_code != 200:
            raise HttpException(responses[auth.status_code])
        auth_response_json = auth.json()
        if auth_response_json["responseStatus"] == "FAILURE":
            raise AuthenticationException(
                "Authentication error: " + auth_response_json["errors"][0]["message"]
            )
        else:
            sessionId = auth_response_json["sessionId"]
            api_url = "https://" + vault + ".veevavault.com/api"
            r = requests.get(api_url, headers={"Authorization": sessionId})
            all_api = r.json()["values"]
            latest_api = all_api[list(all_api)[-1]]
            mainvault = tuple()
            allvaults = dict()
            for vault_details in auth_response_json["vaultIds"]:
                allvaults[vault_details["id"]] = vault_details["name"]
                if vault_details["id"] == auth_response_json["vaultId"]:
                    mainvault = (
                        vault_details["id"],
                        vault_details["name"],
                        latest_api,
                    )
            print(f"Authenticated in {mainvault[1]}")
            return session_details(sessionId, mainvault, allvaults)
    except:
        raise


def parse_args():
    # Parse command line arguments and return parameters
    parser = argparse.ArgumentParser(
        description="An interactive VQL prompt", prog="ivql"
    )
    parser.add_argument("-u", "--user", help="User name")
    parser.add_argument("-p", "--password", help="Password")
    parser.add_argument(
        "-v", "--vault", help='Vault server, excluding ".veevavault.com"'
    )
    return parser.parse_args()


def execute_vql(
    session: session_details,
    vql_query: str,
    limit: int = 0,
    pages: int = 0,
    tokenize: bool = False,
) -> dict:
    try:
        if limit == 0:
            strLimit = ""
        else:
            strLimit = " LIMIT " + str(limit)
        payload = {"q": vql_query + strLimit}
        http_params = {}
        if tokenize:
            http_params["tokenize"] = str(tokenize)
        r = requests.post(
            session.mainvault[2] + "/query",
            params=http_params,
            data=payload,
            headers={"Authorization": session.sessionId},
        )
        response = r.json()
        results = response
        if results["responseStatus"] != "FAILURE":
            print(results["responseStatus"])
            print("Number of results: " + str(results["responseDetails"]["total"]))
            print("Fetching page 1")
        if (
            "responseDetails" in results
        ):  # The response might be a failure and not contain this object
            i = 1
            while "next_page" in response["responseDetails"] and (
                i < pages or pages == 0
            ):  # Check if there is a next page
                i += 1
                print("Fetching page " + str(i))
                r = requests.get(
                    "https://"
                    + urlparse(session.mainvault[2]).netloc
                    + response["responseDetails"]["next_page"],
                    headers={"Authorization": session.sessionId},
                )
                # response = json.loads(r.text)
                response = r.json()
                results["data"].extend(response["data"])
        return results
    except requests.exceptions.ConnectionError:
        return {"error": "Connection Error"}


def main():
    args = parse_args()  # get command line arguments

    vault_session = authorize(args.user, args.password, args.vault)
    try:
        with open("completer.txt", "r") as f:
            vql_completer = WordCompleter(f.read().splitlines())
    except FileNotFoundError:
        print("No autocompletion configuration file found")
        session = PromptSession()
    else:
        session = PromptSession(completer=vql_completer)
    while True:
        query = session.prompt("VQL> ")
        if query.lower() in ("quit", "exit"):
            print("Bye!")
            break
        elif query == "":
            pass
        elif query.lower() == "cls":
            os.system("cls")
        elif not match("SELECT ", query.upper()):
            print("Not a select statement or known command.")
        else:
            query_response = custom_df.cjson_normalize(
                execute_vql(vault_session, query)
            )
            query_response = query_response.expand()
            print(tabulate(query_response, headers="key", tablefmt="github"))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit("Bye!")
