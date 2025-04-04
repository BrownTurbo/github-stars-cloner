import os
#import requests
import httpx
import subprocess
import time
import re
import sys
from datetime import datetime
import json
import argparse
import logging

# Detecting Python 3 for version-dependent implementations
if sys.version_info.major < 3:
    raise Exception("Python's major versions earlier than 3 are not supported!")

logging.basicConfig(level=logging.INFO)

original_dir = os.getcwd()

parser = argparse.ArgumentParser(prog="git-archv",description="Fetch starred repos from GitHub.")
parser.add_argument('--token', type=str, help="GitHub token", required=False)
parser.add_argument('--username', type=str, help="GitHub username", required=False)
parser.add_argument('--apages', type=int, help="Number of API Pages", required=False)
parser.add_argument('--errbreak', type=bool, help="Stop processing and break on ERROR", required=False, nargs='?', metavar='NONE')
parser.add_argument('--errexit', type=bool, help="Stop processing and exit on ERROR", required=False, nargs='?', metavar='NONE')
parser.add_argument('--verbose', type=bool, help="Verbose output", required=False, nargs='?', metavar='NONE')

args = parser.parse_args()

GITHUB_TOKEN = args.token or os.getenv("GITHUB_TOKEN")
GITHUB_USERNAME = args.username or os.getenv("GITHUB_USERNAME")

try:
    API_PAGES = int(args.apages) or -1
except:
     API_PAGES = -1
NUM_API_PAGES = 0

if not GITHUB_TOKEN or not GITHUB_USERNAME:
    raise ValueError("GitHub token and username must be provided via CLI args or environment variables")

verboseOut = args.verbose or  False
breakOnERR = args.errbreak or False
exitOnERR = args.errexit or False

# GitHub API headers for authentication
GITHUB_TOKEN = GITHUB_TOKEN.strip()
Reqheaders = {
    'Authorization': f'token {GITHUB_TOKEN}',
    'Accept': 'application/vnd.github.v3+json'
}

# Set the depth for shallow cloning (use None for full cloning)
CLONE_DEPTH = 1  # Set this to None for full cloning

#CACHE_DIR = 'cache'
CLONED_REPOS_FILE = 'cloned_repos.json'

counting_objects_re = re.compile(r'Counting objects:\s*(\d+)')
compressing_objects_re = re.compile(r'Compressing objects:\s*(\d+)%\s*\((\d+)/(\d+)\)')
deltas_re = re.compile(r'Total\s+(\d+)\s*\(delta\s+(\d+)\),\s*reused\s+(\d+)\s*\(delta\s+(\d+)\)\,\s*pack-reused\s+(\d+)')
recv_objects_re = re.compile(r'Receiving objects\:\s+(\d+)\%\s+\((\d+)\/(\d+)\),\s+([\d.]+)\s+(\w+)\s+\|\s+([\d.]+)\s+(\w+)\/s')
resv_deltas_re = re.compile(r'Resolving deltas\:\s+(\d+)\%\s+\((\d+)\/(\d+)\)\,\s+done')

"""
def load_cached_page(page_num):
    cache_file = os.path.join(CACHE_DIR, f'github_cache_page_{page_num}.json')
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as file:
            return json.load(file)
    return None

def save_page_to_cache(page_num, data):
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    cache_file = os.path.join(CACHE_DIR, f'github_cache_page_{page_num}.json')
    with open(cache_file, 'w') as file:
        json.dump(data, file, indent=4)
"""

def load_cloned_repos():
    if not os.path.exists(CLONED_REPOS_FILE):
        with open(f"{original_dir}/{CLONED_REPOS_FILE}", 'w') as file:
            json.dump([], file, indent=4)
        return []
    try:
        with open(f"{original_dir}/{CLONED_REPOS_FILE}", 'r') as file:
            return json.load(file)
    except json.JSONDecodeError:
        print(f"Error: {CLONED_REPOS_FILE} is corrupted. Creating a new file.")
        with open(f"{original_dir}/{CLONED_REPOS_FILE}", 'w') as file:
            json.dump([], file, indent=4)
        return []
        
def save_cloned_repo(repo_name):
    cloned_repos = load_cloned_repos()
    cloned_repos.append(repo_name)
    with open(f"{original_dir}/{CLONED_REPOS_FILE}", 'w') as file:
        json.dump(cloned_repos, file, indent=4)

def is_repo_cloned(repo_name):
    cloned_repos = load_cloned_repos()
    return repo_name in cloned_repos

def get_branch_name():
    current_branch = subprocess.check_output(['git', 'branch'], text=True)
    current_branch_name = next(line for line in current_branch.splitlines() if line.startswith('* ')).strip()[2:]
    return current_branch_name

def get_remote_repo():
    return subprocess.check_output(['git', 'remote'], text=True).strip()

def attempt_fix_repo():
    __branchN = get_branch_name()
    __remoteN = get_remote_repo()
    proc_rcode = subprocess.call(['git','reset','--hard',__branchN])
    if proc_rcode != 0:
        sys.stderr.write(f"Unexpected error occurred while attempting to fix Repository\n")
        if exitOnERR:
            sys.exit()
    proc_rcode = subprocess.call(['git','reset','--hard',f"{__remoteN}/{__branchN}"])
    if proc_rcode != 0:
        sys.stderr.write(f"Unexpected error occurred while attempting to fix Repository\n")
        if exitOnERR:
            sys.exit()

def attempt_update_repo():
    # Git pull with submodules
    _proc = subprocess.Popen(['git', 'pull', '--recurse-submodules'], stderr=subprocess.PIPE)
    stderr = _proc.stderr.readline()
    if stderr:
        print(stderr)

    # Update submodules recursively
    _proc = subprocess.Popen(['git', 'submodule', 'update', '--init', '--recursive'], stderr=subprocess.PIPE)
    stderr = _proc.stderr.readline()
    if stderr:
        print(stderr)

    # Fetch all updates and prune stale references
    _proc = subprocess.Popen(['git', 'fetch', '--all'], stderr=subprocess.PIPE)
    stderr =  _proc.stderr.readline()
    if stderr:
        print(stderr)
    _proc = subprocess.Popen(['git', 'fetch', '--prune', '--tags'], stderr=subprocess.PIPE)
    stderr = _proc.stderr.readline()
    if stderr:
        print(stderr)

def get_starred_repos():
    repos = []
    url = f'https://api.github.com/users/{GITHUB_USERNAME}/starred'
    global NUM_API_PAGES
    print("Starting to fetch starred repositories from GitHub...")
    
    while url:
        print(f"Fetching repositories from {url}...")
        
        try:
            # Request starred repositories
            #response = requests.get(url, headers=Reqheaders, timeout=(5, 10))
            response =  httpx.get(url, headers=Reqheaders, timeout=10)
        
            if response.status_code == 200:
                repos_on_page = response.json()
                if repos_on_page:
                    repos.extend(repos_on_page)
                    print(f"Found {len(repos_on_page)} repositories on this page.")
                else:
                    print(f"No more repositories found.")
                    break

                # Check for pagination in the 'Link' header
                link_header = response.headers.get('Link')
                if link_header:
                    url = None
                    links = link_header.split(", ")
                    for link in links:
                        if 'rel="next"' in link:
                            # Extract the next page URL
                            url = link[link.find("<") + 1: link.find(">")]
                            break
                else:
                    url = None
            
                if NUM_API_PAGES >= API_PAGES and API_PAGES != -1:
                    print("No more pages to fetch.")
                    break
            
                if not url:
                    print("No more pages to fetch.")
                    break
                else:
                    NUM_API_PAGES += 1
            
                # Fetch and handle rate limit information
                remaining = int(response.headers.get('X-RateLimit-Remaining', 0))
                print(f"Remaining requests: {remaining}")

                # If we're out of requests, wait until the rate limit resets
                if remaining == 0:
                    # Calculate how long to wait until rate limit resets
                    reset_time = int(response.headers.get('X-RateLimit-Reset', 0))
                    reset_timestamp = datetime.fromtimestamp(reset_time)
                    sleep_duration = (reset_timestamp - datetime.now()).total_seconds()
                    print(f"Rate limit hit! Waiting for {int(sleep_duration)} seconds (until {reset_timestamp})...")
                    time.sleep(sleep_duration)
                else:
                    # Add a cooldown of 2 .5seconds between calls to prevent hitting rate limits too quickly
                    print("Waiting for 2 seconds and half before the next request...")
                    time.sleep(2.5)
            else:
                sys.stderr.write(f"Error: Failed to fetch repositories (HTTP {response.status_code}).")
                sys.stderr.write(f"Response content: {response.text}\n")
                break
        except httpx.RequestError as e:
            sys.stderr.write(f"Failed to fetch Repositories list from API: {e}\n")
        """except requests.RequestException as e:
            sys.stderr.write(f"Failed to fetch Repositories list from API: {e}\n")
        except requests.exceptions.Timeout:
            print("Timed out...")"""
            
    print(f"\nFinished fetching repositories. Total repositories found: {len(repos)}.")
    return repos

def check_for_wiki(repo_name, headers):
    # URL for the repository information
    repo_api_url = f"https://api.github.com/repos/{repo_name}"
    
    try:
        # Request repository data
        #response = requests.get(repo_api_url, headers=Reqheaders)
        response =  httpx.get(repo_api_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            repo_data = response.json()
            
            #print(f"Repository data for {repo_name}: {json.dumps(repo_data, indent=2)}")

            # Fetch and handle rate limit information
            remaining = int(response.headers.get('X-RateLimit-Remaining', 0))
            print(f"Remaining requests: {remaining}")

            # If we're out of requests, wait until the rate limit resets
            if remaining == 0:
                # Calculate how long to wait until rate limit resets
                reset_time = int(response.headers.get('X-RateLimit-Reset', 0))
                reset_timestamp = datetime.fromtimestamp(reset_time)
                sleep_duration = (reset_timestamp - datetime.now()).total_seconds()
                print(f"Rate limit hit! Waiting for {int(sleep_duration)} seconds (until {reset_timestamp})...")
                time.sleep(sleep_duration)
            else:
                # Check if the repository has a wiki enabled
                if repo_data.get("has_wiki", False):
                    print(f"Wiki is enabled for {repo_name}.")
                    return True
                else:
                    print(f"No wiki available for {repo_name}.")
                    return False
        else:
            print(f"Error fetching repository data: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"Error occurred while checking for wiki: {e}")
        return False

def clone_repo_with_wiki(repo_url, repo_name, language, owner):
    # Set the folder name as "User@RepoName"
    folder_name = f"{owner}@{repo_name.split('/')[-1]}"

    # Check if the repository has a wiki
    if check_for_wiki(repo_name, Reqheaders):
        wiki_url = repo_url.replace('.git', '.wiki.git')
        wiki_folder = f'{folder_name}-Wiki'
        
        # Directory for the language, default to "Unknown" if no language specified
        if not language:
            language = "Unknown"
    
        # Create language directory if it doesn't exist
        if not os.path.exists(f'{original_dir}/{language}'):
            print(f"Creating directory: {language}")
            os.makedirs(f'{original_dir}/{language}')
    
        # Change into the language directory
        print(f"Changing to directory: {language}")
        os.chdir(f'{original_dir}/{language}')
        
        if not os.path.exists(wiki_folder):
            print(f"Cloning the wiki into {wiki_folder}...")

            # Clone the wiki repo
            wiki_clone_command = ['git', 'clone','--progress', wiki_url, wiki_folder]

            # Add the --depth option if cloning depth is specified
            if CLONE_DEPTH is not None:
                wiki_clone_command += ['--depth', str(CLONE_DEPTH)]

            print(f"Executing command: {' '.join(wiki_clone_command)}")
            try:
                wiki_process = subprocess.Popen(wiki_clone_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                _, wiki_stderr =  wiki_process.communicate()


                if wiki_process.returncode != 0 or wiki_stderr:
                    if wiki_process.returncode == 128:
                        print(f"Wiki clone is possibly failed (Code {wiki_process.returncode})")
                    else:
                        print(f"Wiki clone is possibly failed (Code {wiki_process.returncode}): {wiki_stderr}")
                else:
                    print(f"Wiki successfully cloned into {wiki_folder}")
            except subprocess.CalledProcessError as e:
                sys.stderr.write(f"Error: Subprocess error occurred while cloning Wiki of {repo_url}. Error: {e.stderr} (Exit Code: {e.returncode})\n")
                if exitOnERR:
                    sys.exit()
            except Exception as ex:
                sys.stderr.write(f"Unexpected error occurred while cloning Wiki of {repo_url}: {ex}\n")
                if exitOnERR:
                    sys.exit()
        else:
            if os.path.exists(f'{folder_name}/.git'):
                print(f"Wiki Directory {wiki_folder} is already created earlier and is a Git Repository")
            else:
                print(f"Wiki Directory {wiki_folder} is already created earlier for some reason")
        # Move back to the original directory
        print(f"Returning to the parent directory.")
        os.chdir(original_dir) 

def clone_repo(repo_url, repo_name, language, owner):
    # Set the folder name as "User@RepoName"
    folder_name = f"{owner}@{repo_name.split('/')[-1]}"
    repo_path = f"{original_dir}/{language}/{folder_name}"
    if os.path.exists(f'{repo_path}/.git') or is_repo_cloned(folder_name):
        print(f"{folder_name} is already cloned, proceeding...")
        try:
            os.chdir(repo_path)
            print(f"Attempting to fix any problem on {repo_name}...")
            attempt_fix_repo()
            print(f"Updating repository {repo_name}...")
            attempt_update_repo()
            print(f"Repository {repo_name} updated successfully.\n")
        finally:
            print("Returning to the parent directory.")
            os.chdir(original_dir)
        return

    # Directory for the language, default to "Unknown" if no language specified
    if not language:
        language = "Unknown"
    
    # Create language directory if it doesn't exist
    if not os.path.exists(f'{original_dir}/{language}'):
        print(f"Creating directory: {language}")
        os.makedirs(f'{original_dir}/{language}')
    
    # Change into the language directory
    print(f"Changing to directory: {language}")
    os.chdir(f'{original_dir}/{language}')
    
    # Create the repository directory
    if not os.path.exists(f'{original_dir}/{language}/{folder_name}'):
        print(f"Creating repository directory: {folder_name}")
        os.makedirs(f'{original_dir}/{language}/{folder_name}')
    else:
        if os.path.exists(f'{folder_name}/.git'):
            print(f"Directory {folder_name} is already created earlier and is a Git Repository")
            
            os.chdir(original_dir) 
            if not is_repo_cloned(folder_name):
                save_cloned_repo(folder_name)
            #return
        else:
            print(f"Directory {folder_name} is already created earlier for some reason")
            os.chdir(original_dir) 

    # Change into the repository directory
    os.chdir(f'{original_dir}/{language}/{folder_name}')
    
    # Prepare the git clone command
    git_command = ['git', 'clone', '--progress', repo_url, '.']  # Clone directly into the current directory
    
    # Add the --depth option if cloning depth is specified
    if CLONE_DEPTH is not None:
        git_command += ['--depth', str(CLONE_DEPTH)]

    print(f"Executing command: {' '.join(git_command)}")
    
    # Execute the git clone command and capture the output
    try:
        # Start the git clone process using subprocess.Popen to capture real-time output
        process = subprocess.Popen(
            git_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=False,
            bufsize=1,  # Line-buffered
            encoding='utf-8'
        )

        # Variables to store progress info
        total_objects = 0
        compressed_objects = 0
        total_cobjects = 0
        total_deltas = 0
        reused_objects = 0
        reused_deltas = 0
        reused_pobjects = 0
        rsv_delt_percent = 0
        rsv_delt_resolved = 0
        rsv_delt_total = 0
        rcv_obj_percent = 0
        rcv_obj_received = 0
        rcv_obj_total = 0
        rcv_obj_size = 0
        rcv_obj_size_unit = 0
        rcv_obj_speed = 0
        rcv_obj_speed_unit = 0

        # Real-time printing of the git clone process
        while True:
            output = process.stdout.readline()
            if isinstance(output, bytes):
                line = output.decode('utf-8', errors='replace')
            else:
                line = output
            error_output = process.stderr.readline()

            if '\r' in line:
                line = line.split('\r')[-1]
                line = line.strip()

            # Analyze stdout for specific information
            if line:
                # Match counting objects
                match = counting_objects_re.search(line)
                if match:
                    total_cobjects = int(match.group(1))
                    continue

                # Match compressing objects
                match = compressing_objects_re.search(line)
                if match:
                    compressed_objects = int(match.group(1))
                    continue
                
                # Match delta information
                match = deltas_re.search(line)
                if match:
                    total_objects = int(match.group(1))
                    total_deltas = int(match.group(2))
                    reused_objects = int(match.group(3))
                    reused_deltas = int(match.group(4))
                    reused_pobjects = int(match.group(5))
                    continue

                # Match receiving objects
                match = recv_objects_re.search(line)
                if match:
                    rcv_obj_percent = int(match.group(1))
                    rcv_obj_received = int(match.group(2))
                    rcv_obj_total = int(match.group(3))
                    rcv_obj_size = int(match.group(4))
                    rcv_obj_size_unit = int(match.group(5))
                    rcv_obj_speed = int(match.group(6))
                    rcv_obj_speed_unit = int(match.group(7))
                    continue

                # Match resolving deltas
                match = resv_deltas_re.search(line)
                if match:
                    rsv_delt_percent = int(match.group(1))
                    rsv_delt_resolved = int(match.group(2))
                    rsv_delt_total = int(match.group(3))
                    continue

            print(f"remote: Counting objects: {total_cobjects}\n")
            print(f"remote: Compressing objects: {compressed_objects}%\n")
            print(f"Total {total_objects} (delta {total_deltas}), reused {reused_objects} (delta {reused_deltas}), pack-reused {reused_pobjects}\n")
            print(f"Receiving objects: {rcv_obj_percent}% ({rcv_obj_received}/{rcv_obj_total}), {rcv_obj_size} {rcv_obj_size_unit} | {rcv_obj_speed} {rcv_obj_speed_unit}/s\n")
            print(f"Resolving deltas: {rsv_delt_percent}% ({rsv_delt_resolved}/{rsv_delt_total}), done\n")
            sys.stdout.flush()

            if error_output:
                print(f"[stderr] {error_output.strip()}")

            # Break the loop if the process is finished and both stdout and stderr are done
            if process.poll() is not None and not line and not error_output:
                break

        # Final exit code of the git clone process
        return_code = process.wait()

        """
        # Check the size of the shallow clone
        repo_size_command = ['du', '-sh', f'{original_dir}/{language}/{folder_name}']
        size_process = subprocess.Popen(
            repo_size_command,	
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        size_output, _ =  size_process.communicate()
        print(f"Clone Repository'Size: {size_output.strip()}")
        """

        if return_code == 0:
            save_cloned_repo(folder_name)
            print(f"Success: {repo_name} successfully cloned into '{original_dir}/{language}/{folder_name}'")
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"Error: Subprocess error occurred while cloning {repo_url}. Error: {e.stderr} (Exit Code: {e.returncode})\n")
        if exitOnERR:
            sys.exit()
    except Exception as ex:
        sys.stderr.write(f"Unexpected error occurred while cloning {repo_url}: {ex}\n")
        if exitOnERR:
            sys.exit()

    if os.path.exists(f'{folder_name}/.git'):
        print(f"Attempting  to fix any problem on {repo_name}...")
        attempt_fix_repo()
    
    # Move back to the original directory
    print(f"Returning to the parent directory.")
    os.chdir(original_dir) 

def main():
    print(f"\nStarting process for user: {GITHUB_USERNAME}")
    
    # Get the starred repositories list
    starred_repos = get_starred_repos()
    
    if not starred_repos:
        sys.stderr.write("No repositories found or error fetching data.\n")
        if exitOnERR:
            sys.exit()
        return

    print(f"\nProcessing {len(starred_repos)} repositories...")

    # Loop through each starred repo and clone it
    for i, repo in enumerate(starred_repos, start=1):
        repo_url = repo['clone_url']
        repo_name = repo['full_name']  # Repo in format "owner/repo"
        language = repo['language']  # Get the language of the repo
        repo_size = repo['size']  # Size of the repository in KB
        owner = repo['owner']['login']  # Get the owner of the repo

        # Display detailed info about the current repository
        print(f"\nProcessing repository {i}/{len(starred_repos)}:")
        print(f"  - Repository Name: {repo_name}")
        print(f"  - Clone URL: {repo_url}")
        print(f"  - Language: {language if language else 'Unknown'}")
        print(f"  - Repository Size: {repo_size} KB")
        print(f"  - Owner: {owner}")

        clone_repo(repo_url, repo_name, language, owner)
        clone_repo_with_wiki(repo_url, repo_name, language, owner)

    print("\nProcess completed. All repositories have been cloned.")

try:
    if __name__ == '__main__':
        main()
except KeyboardInterrupt:
    print('Exiting...')
except Exception as e:
    sys.stderr.write(f"Sorry, something went wrong> {e}\n")
