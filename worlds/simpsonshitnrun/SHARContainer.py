import json
import zipfile
from pathlib import Path
from worlds.Files import APPlayerContainer


class SHARContainer(APPlayerContainer):
    game: str = "The Simpsons Hit And Run"

    def __init__(
        self,
        ID,
        TitleID,
        card_table,
        traffic_table,
        mission_locks,
        base_path: str,
        output_directory: str,
        player=None,
        player_name: str = "",
        server: str = "",
    ):
        self.ID = ID
        self.TitleID = TitleID
        self.card_table = card_table
        self.traffic_table = traffic_table
        self.mission_locks = mission_locks
        self.output_directory = Path(output_directory)
        self.file_path = Path(base_path)
        self.patch_file_ending = ".apshar"
        self.output_directory.mkdir(parents=True, exist_ok=True)
        container_path = self.output_directory / self.file_path
        super().__init__(container_path, player, player_name, server)

    def write_contents(self, opened_zipfile: zipfile.ZipFile):
        filename = "SHAR.ini"
        ini_data = f"[IDENTIFIER]\nID={self.ID}\nTitleID={self.TitleID}\n\n"

        i = 1
        for card in self.card_table:
            ini_data += "[CARD]\n"
            ini_data += f"Name=card{card['level'][-1]}{i}\n"
            ini_data += f"CardName={card['name']}\n"
            ini_data += f"X={card['X']}\n"
            ini_data += f"Y={card['Y']}\n"
            ini_data += f"Z={card['Z']}\n"
            ini_data += f"APID={card['id']}\n\n"
            i = i + 1 if i < 7 else 1

        for car in self.traffic_table:
            ini_data += "[TRAFFIC]\n"
            ini_data += f"Name={car}\n\n"

        for mission, car in self.mission_locks.items():
            ini_data += "[MISSIONLOCK]\n"
            ini_data += f"Mission={str(mission)}\n"
            ini_data += f"Car={car}\n\n"

        opened_zipfile.writestr(filename, ini_data)
        super().write_contents(opened_zipfile)


def gen(output_directory, mod_name, ID, TitleID, card_table, traffic_table, mission_locks, player):
    output_directory = Path(output_directory)
    mod_dir = output_directory / mod_name
    mod_file_path = mod_dir.with_suffix(".apshar")
    mod = SHARContainer(
        ID,
        TitleID,
        card_table,
        traffic_table,
        mission_locks,
        mod_file_path,
        output_directory,
        player
    )
    mod.write()
