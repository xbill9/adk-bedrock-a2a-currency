#!/bin/bash


PROJECT_FILE="$HOME/project_id.txt"

if [ ! -f "$PROJECT_FILE" ]; then
  echo "--- Setting Google Cloud Project ID File ---"
  # Prompt the user for input
  read -p "Please enter your Google Cloud project ID: " user_project_id

  # Check if the user entered anything
  if [[ -z "$user_project_id" ]]; then
    echo "Error: No project ID was entered."
    exit 1 # Exit the script with an error code
  fi

  echo "You entered: $user_project_id"

  # Write the project ID to the file
  # Using > will overwrite the file if it exists
  echo "$user_project_id" > "$PROJECT_FILE"

  # Check if the write operation was successful
  if [[ $? -eq 0 ]]; then
    echo "Successfully saved project ID."
  else
    echo "Error: Failed saving your project ID:  $user_project_id."
    exit 1
  fi
else
  echo "Project ID file found, skipping."
fi

echo "--- Authentication Method ---"
read -p "Do you want to use a Gemini API Key for authentication? (y/n): " use_gemini_key

if [[ "$use_gemini_key" == "y" || "$use_gemini_key" == "Y" ]]; then
    KEY_FILE="$HOME/gemini.key"

    echo "--- Setting Google Cloud Gemini Key File ---"
    # Prompt the user for input (silent, so the key is not shown on screen)
    read -s -p "Please enter your Google Cloud Gemini Key: " user_gemini_key
    echo

    # Check if the user entered anything
    if [[ -z "$user_gemini_key" ]]; then
      echo "Error: No Gemini Key was entered."
      exit 1 # Exit the script with an error code
    fi

    # Write the key to the file, readable only by the current user
    # Using > will overwrite the file if it exists
    (umask 077 && echo "$user_gemini_key" > "$KEY_FILE")

    # Check if the write operation was successful
    if [[ $? -eq 0 ]]; then
      chmod 600 "$KEY_FILE"
      echo "Successfully saved Gemini Key."
    else
      echo "Error: Failed saving your Gemini Key."
      exit 1
    fi

    export GOOGLE_API_KEY=$user_gemini_key
else
    # Check if gcloud auth is active
    if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q .; then
        echo "No active gcloud account found. Please log in."
        gcloud auth login
    fi

    gcloud config set project $(cat ~/project_id.txt)

    # Check for application default credentials
        gcloud auth application-default login
fi

export PATH=$PATH:$HOME/.local/bin

echo "--- Setup complete ---"
