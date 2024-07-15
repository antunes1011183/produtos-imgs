def remove_specific_string(file_path, target_string):
    # Abrir o arquivo para leitura
    with open(file_path, 'r') as file:
        lines = file.readlines()
    
    # Processar cada linha para remover a string específica
    modified_lines = [line.replace(target_string, '') for line in lines]
    
    # Abrir o arquivo para escrita e gravar as linhas modificadas
    with open(file_path, 'w') as file:
        file.writelines(modified_lines)

# Caminho do arquivo
file_path = 'pricetab.txt'

# String específica a ser removida
target_string = " |   |155|BLUE|095 190|RED |BLUE|1||||||||||||"

# Chamar a função para remover a string específica em cada linha
remove_specific_string(file_path, target_string)
