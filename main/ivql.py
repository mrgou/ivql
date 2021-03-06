import pandas as pd
import tabulate
import requests
import json
import argparse
import time
import os
import configparser
import sys

from dataclasses import dataclass
from http.client import responses
from urllib.parse import urlparse
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
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
        url = f"https://{vault}.veevavault.com/api/v20.3/auth"
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
        "vault", help='Vault server, excluding ".veevavault.com"'
    )
    return parser.parse_args()


def get_config():
    def createFolder(directory):
        try:
            if not os.path.exists(directory):
                os.makedirs(directory)
        except OSError:
            print("Error: Creating directory. " + directory)

    config = configparser.ConfigParser()
    settings = {"delim": ",", "outdir": "."}
    try:  # If the config file loads successfully (i.e. it is well-formed)
        config.read("ivql.ini")
        if config.has_option("DEFAULT", "delimiter"):
            settings["delim"] = config["DEFAULT"]["delimiter"]
        if config.has_option("DEFAULT", "outdir"):
            settings["outdir"] = config["DEFAULT"]["outdir"]
            createFolder(settings["outdir"])
    except configparser.MissingSectionHeaderError:
        print(
            "Could not load the config file. It may not be well formed. Default values will be used."
        )
    return settings


def execute_vql(
    session: session_details,
    vql_query: str,
    pages: int = 0,
    tokenize: bool = False,
) -> dict:
    try:
        payload = {"q": vql_query}
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
    if args.user is None:
        args.user = input("User name: ")
    if args.password is None:
        args.password = input("Password: ")

    config = get_config()

    try:
        vault_session = authorize(args.vault, args.user, args.password)
    except (
        requests.exceptions.ConnectionError,
        HttpException,
        AuthenticationException,
    ) as e:
        sys.exit(e)
    
    vql_history = FileHistory('ivql.history')

    try:
        with open("completer.txt", "r") as f:
            vql_completer = WordCompleter(f.read().splitlines())
    except FileNotFoundError:
        print("No autocompletion configuration file found")
        session = PromptSession(history=vql_history)
    else:
        session = PromptSession(completer=vql_completer, history=vql_history, complete_while_typing=False)
    while True:
        query = session.prompt("VQL> ")
        if query.lower() in ("quit", "exit"):
            print("Bye!")
            break
        elif query == "":
            pass
        elif query.lower() == "cls":
            os.system("cls")
        elif query == 'delimiter':
            print('Current delimiter:' + config['delim'])
        elif query.lower()[:9] == "delimiter":
            config['delim'] = query.split(" ")[-1]
        elif query.lower()[:6] == "export":
            exp_format = query.split(" ")[-1]
            timestamp = time.strftime("%Y%m%d%H%M%S", time.localtime())
            filename = os.path.join(config["outdir"], timestamp)
            try:
                if exp_format == "csv":
                    query_data.to_csv(
                        filename + ".csv",
                        sep=config["delim"],
                        encoding="utf-8-sig",
                        index=False,
                    )
                    print(f"Results exported to {filename}.csv")
                elif exp_format == "json":
                    with open(filename + ".json", "w", encoding="utf-8") as f:
                        json.dump(vql_results, f)
                        print(f"Results exported to {filename}.json")
                else:
                    print(f"Unrecognized format {exp_format}")
            except NameError:
                print("No query results to export.")
        elif query.lower()[:6] != "select":
            print("Not a select statement or known command.")
        else:
            vql_results = execute_vql(vault_session, query)
            if vql_results["responseStatus"] == "FAILURE":
                print(
                    f"Error: {vql_results['errors'][0]['type']}: {vql_results['errors'][0]['message']}"
                )
            else:
                query_data = custom_df.cjson_normalize(vql_results["data"])
                query_data = query_data.expand()
                print(
                    tabulate(
                        query_data,
                        headers="keys",
                        tablefmt="github",
                        showindex=False,
                    )
                )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit("Bye!")
