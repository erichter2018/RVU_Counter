import os
import yaml
import shutil

def migrate():
    print("Starting data migration...")
    
    # Files to move
    db_file = "rvu_records.db"
    settings_file = "rvu_settings.yaml"
    
    # Destination folders
    data_folder = "data"
    settings_folder = "settings"
    
    # 1. Migrate Database
    if os.path.exists(db_file):
        dest_db = os.path.join(data_folder, db_file)
        print(f"Moving {db_file} to {dest_db}")
        shutil.move(db_file, dest_db)
    else:
        print(f"No {db_file} found in root.")

    # 2. Split and Migrate Settings
    if os.path.exists(settings_file):
        print(f"Splitting {settings_file}...")
        with open(settings_file, 'r') as f:
            full_config = yaml.safe_load(f)
            
        # Define Rule-related keys
        rule_keys = ['direct_lookups', 'rvu_table', 'classification_rules']
        
        # Create Rules config
        rules_config = {k: full_config[k] for k in rule_keys if k in full_config}
        
        # Create User Preferences config
        user_config = {k: v for k, v in full_config.items() if k not in rule_keys}
        
        # Save new files
        new_settings_path = os.path.join(settings_folder, "rvu_settings.yaml")
        new_rules_path = os.path.join(settings_folder, "rvu_rules.yaml")
        
        print(f"Writing user preferences to {new_settings_path}")
        with open(new_settings_path, 'w') as f:
            yaml.dump(user_config, f, sort_keys=False)
            
        print(f"Writing RVU rules to {new_rules_path}")
        with open(new_rules_path, 'w') as f:
            yaml.dump(rules_config, f, sort_keys=False)
            
        # Keep the original as backup for now, rename it
        shutil.move(settings_file, settings_file + ".bak")
        print(f"Original settings backed up to {settings_file}.bak")
    else:
        print(f"No {settings_file} found in root.")

    # 3. Handle Legacy JSON (if exists)
    legacy_json = "rvu_records.json"
    if os.path.exists(legacy_json):
        dest_json = os.path.join(data_folder, legacy_json)
        print(f"Moving legacy {legacy_json} to {dest_json}")
        shutil.move(legacy_json, dest_json)

    print("Migration complete!")

if __name__ == "__main__":
    migrate()







