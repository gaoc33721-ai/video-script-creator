import PyPDF2
import sys

def read_pdf(file_path):
    try:
        with open(file_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            print(f"=== PDF Info: {file_path} ===")
            print(f"Total Pages: {len(reader.pages)}")
            
            print("\n=== PDF Content ===")
            for i in range(len(reader.pages)):
                page = reader.pages[i]
                text = page.extract_text()
                print(f"\n--- Page {i+1} ---")
                print(text)
                
    except Exception as e:
        print(f"Error reading PDF file: {e}")

if __name__ == "__main__":
    read_pdf(sys.argv[1])
