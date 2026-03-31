import pandas as pd
import sys

def search_excel(file_path):
    try:
        df = pd.read_excel(file_path)
        
        # We want to see some unique products to understand what we're working with
        print("=== Unique Categories ===")
        print(df['Category'].dropna().unique()[:10])
        
        print("\n=== Let's look at a specific product's English features ===")
        # Filter for a specific category and English language to see full feature descriptions
        english_mask = df['language'].str.contains('英语|全球通用版', na=False)
        sample_df = df[english_mask].dropna(subset=['Feature Description']).head(10)
        
        for _, row in sample_df.iterrows():
            print(f"\nProduct: {row['Brand']} {row['Category']} ({row['model']})")
            print(f"Feature: {row['Feature Name']}")
            print(f"Tagline: {row['Tagline']}")
            print(f"Description: {row['Feature Description']}")
            
    except Exception as e:
        print(f"Error reading Excel file: {e}")

if __name__ == "__main__":
    search_excel(sys.argv[1])
