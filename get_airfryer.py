import pandas as pd
import sys

def get_airfryer_features(file_path):
    try:
        df = pd.read_excel(file_path)
        
        # Filter for Air Fryers and English/Global language
        airfryer_mask = (df['Category'] == '空气炸锅') & df['language'].str.contains('英语|全球通用版', na=False)
        airfryer_df = df[airfryer_mask].dropna(subset=['Feature Description'])
        
        print("=== Air Fryer Models Found ===")
        models = airfryer_df['model'].unique()
        print(models)
        
        if len(models) > 0:
            target_model = models[0]
            print(f"\n=== Features for {target_model} ===")
            model_df = airfryer_df[airfryer_df['model'] == target_model]
            
            for _, row in model_df.iterrows():
                print(f"- {row['Feature Name']}: {row['Tagline']}")
                print(f"  {row['Feature Description']}\n")
            
    except Exception as e:
        print(f"Error reading Excel file: {e}")

if __name__ == "__main__":
    get_airfryer_features(sys.argv[1])
