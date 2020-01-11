#!/usr/bin/env python

import yaml
import os
import sys
import random
import numpy as np
import copy
from pymatgen.ext.matproj import MPRester
from configuration.config import MP_api_key
from pymatgen.io.vasp import Poscar
from pymatgen.analysis.magnetism.analyzer import \
    CollinearMagneticStructureAnalyzer
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from pymatgen.core.periodic_table import Element
from pymatgen.core.structure import Structure


class LoadYaml:

    def __init__(self, load_path):
        if os.path.exists(load_path):
            try:
                with open(load_path, 'r') as loadfile:
                    loaded_dictionary = yaml.safe_load(loadfile)
                    self.loaded_dictionary = loaded_dictionary
            except ScannerError:
                print('Invalid .yml; try again or use default dictionary')
                sys.exit(1)
        else:
            print('Path to %s does not exist' % load_path)
            sys.exit(1)

        try:
            self.mpids = self.loaded_dictionary['MPIDs']
        except KeyError:
            print('MPIDs not in %s; invalid input file' % load_path)
            sys.exit(1)
        try:
            self.paths = self.loaded_dictionary['PATHs']
        except KeyError:
            print('PATHs not in %s; invalid input file' % load_path)
            sys.exit(1)
        try:
            self.calculation_type = self.loaded_dictionary['Calculation_Type']
        except KeyError:
            print('Calculation_Type not in %s; invalid input file' % load_path)
            sys.exit(1)
        try:
            self.relaxation_set = self.loaded_dictionary['Relaxation_Set']
        except KeyError:
            print('Relaxation_Set not in %s; invalid input file' % load_path)
            sys.exit(1)
        try:
            self.magnetization_scheme = self.loaded_dictionary['Magnetization_Scheme']
        except BaseException:
            print(
                'Magnetization_Scheme not in %s; invalid input file' %
                load_path)
            sys.exit(1)
        try:
            self.incar_tags = self.loaded_dictionary['INCAR_Tags']
        except BaseException:
            print('INCAR_Tags not in %s; invalid input file' % load_path)
            sys.exit(1)
        try:
            self.max_submissions = self.loaded_dictionary['Max_Submissions']
        except KeyError:
            print('Max_Submissions not in %s; invalid input file' % load_path)
            sys.exit(1)


class PmgStructureObjects:
    def __init__(self, mpids, paths):
        self.mpids = mpids
        self.paths = paths
        self.structures_dict = {}
        self.structure_number = 1

        self.mpid_structures()
        self.path_structures()

    def mpid_structures(self):
        for mpid in self.mpids:
            with MPRester(MP_api_key) as m:
                try:
                    structure = m.get_structures(mpid, final=True)[0]
                    structure_key = str(structure.formula) + ' ' + str(self.structure_number)
                    self.structures_dict[structure_key] = structure
                    self.structure_number += 1
                except BaseException:
                    print('%s is not a valid mp-id' % mpid)
                    continue

    def path_structures(self):
        for path in self.paths:
            try:
                poscar = Poscar.from_file(path)
                structure = poscar.structure
                structure_key = str(structure.formula) + ' ' + str(self.structure_number)
                self.structures_dict[structure_key] = structure
                self.structure_number += 1
            except FileNotFoundError:
                print('%s path does not exist' % path)
                continue
            except UnicodeDecodeError:
                print('%s likely not a valid CONTCAR or POSCAR' % path)
                continue
            except OSError:
                print('%s likely not a valid CONTCAR or POSCAR' % path)

class Magnetism:
    def __init__(self, structures_dict, magnetization_dict, num_tries=100):
        self.structures_dict = structures_dict
        self.magnetization_dict = magnetization_dict
        self.magnetized_structures_dict = {}
        self.num_tries = num_tries
        self.structure_number = 1
        self.unique_magnetizations = {}

        self.get_magnetic_structures()

    def random_antiferromagnetic(self, ferro_magmom, used_enumerations, num_afm, num_tries):
        # checks if all unique iterations complete OR too many tries achieved
        if num_afm == 0 or num_tries == 0:
            return used_enumerations
        # checks if proposed enumeration is the ferromagnetic enumeration
        dont_use = False
        antiferro_mag_scheme = random.choices([-1, 1], k=len(ferro_magmom))
        antiferro_mag = np.multiply(antiferro_mag_scheme, ferro_magmom)
        if np.array_equal(antiferro_mag, np.array(ferro_magmom)):
            dont_use = True
        # checks if proposed scheme is an already existing antiferromagnetic scheme
        for used_enumeration in used_enumerations:
            if np.array_equal(antiferro_mag, used_enumeration):
                dont_use = True
        # if dont_use = True: tries again with num_tries - 1
        if dont_use is True:
            return self.random_antiferromagnetic(ferro_magmom, used_enumerations,
                                            num_afm, num_tries - 1)
        # else: appends to used_enumerations: tries again with num_rand - 1
        else:
            used_enumerations.append(antiferro_mag)
            return self.random_antiferromagnetic(ferro_magmom, used_enumerations,
                                            num_afm - 1, num_tries)

    def afm_structures(self, structure_key, ferro_structure):
        # sets magnetism on a structures key and assigns to self.magnetized_structures_dict
        if set(ferro_structure.site_properties["magmom"]) == set([0]):
            print("%s is not magnetic; ferromagnetic structure to be run"
                    % str(ferro_structure.formula))
            self.magnetized_structures_dict[structure_key]['FM'] = ferro_structure
            self.unique_magnetizations[structure_key]['FM'] = ferro_structure.site_properties["magmom"]
        else:
            random_enumerations = self.random_antiferromagnetic(
                ferro_structure.site_properties["magmom"], [],
                self.magnetization_dict['Max_antiferro'], self.num_tries)
            afm_enum_number = 1
            for enumeration in random_enumerations:
                antiferro_structure = ferro_structure.copy()
                for magmom_idx in range(len(antiferro_structure.site_properties["magmom"])):
                    antiferro_structure.replace(magmom_idx, antiferro_structure.species[magmom_idx], properties={'magmom': enumeration[magmom_idx] + 0})
                    afm_key = 'AFM' + str(afm_enum_number)
                self.magnetized_structures_dict[structure_key][afm_key] = antiferro_structure
                self.unique_magnetizations[structure_key][afm_key] = antiferro_structure.site_properties["magmom"]
                afm_enum_number += 1

    def get_magnetic_structures(self):
        # assigns magnetism to structures. returns the magnetic get_structures
        # num_rand and num_tries only used for random antiferromagnetic assignment
        for structure in self.structures_dict.values():
            collinear_object = CollinearMagneticStructureAnalyzer(
                structure, overwrite_magmom_mode="replace_all")
            ferro_structure = collinear_object.get_ferromagnetic_structure()
            structure_key = str(structure.formula) + ' ' + str(self.structure_number)
            self.unique_magnetizations[structure_key] = {}
            self.magnetized_structures_dict[structure_key] = {}

            if self.magnetization_dict['Scheme'] == 'preserve':
                self.magnetized_structures_dict[structure_key]['preserve'] = structure
                try:
                    self.unique_magnetizations[structure_key]['preserve'] = structure.site_properties["magmom"]
                except KeyError:
                    self.unique_magnetizations[structure_key]['preserve'] = ferro_structure.site_properties["magmom"]

            elif self.magnetization_dict['Scheme'] == 'FM':
                self.magnetized_structures_dict[structure_key]['FM'] = ferro_structure
                self.unique_magnetizations[structure_key]['FM'] = ferro_structure.site_properties["magmom"]

            elif self.magnetization_dict['Scheme'] == 'AFM':
                self.afm_structures(structure_key, ferro_structure)

            elif self.magnetization_dict['Scheme'] == 'FM+AFM':
                self.magnetized_structures_dict[structure_key]['FM'] = ferro_structure
                self.unique_magnetizations[structure_key]['FM'] = ferro_structure.site_properties["magmom"]
                self.afm_structures(structure_key, ferro_structure)

            else:
                print('Magnetization Scheme %s not recognized; fatal error' % self.magnetization_dict['Scheme'])
                sys.exit(1)

            self.structure_number += 1


class CalculationType:
    def __init__(self, magnetic_structures_dict, calculation_dict):
        self.magnetic_structures_dict = magnetic_structures_dict
        self.calculation_dict = calculation_dict
        self.calculation_structures_dict = copy.deepcopy(self.magnetic_structures_dict)
        self.unique_defect_sites = None

        self.alter_structures()

    def structure_rescaler(self, structure):
        if len(structure.species) <= 2:
            structure.make_supercell([4, 4, 4])
        elif len(structure.species) <= 4:
            structure.make_supercell([3, 3, 3])
        elif len(structure.species) <= 7:
            structure.make_supercell([3, 3, 2])
        elif len(structure.species) <= 10:
            structure.make_supercell([3, 2, 2])
        elif len(structure.species) <= 16:
            structure.make_supercell([2, 2, 2])
        elif len(structure.species) <= 32:
            structure.make_supercell([2, 2, 1])
        elif len(structure.species) <= 64:
            structure.make_supercell([2, 1, 1])
        else:
            pass
        return structure

    def get_unique_sites(self, structure):
        SGA = SpacegroupAnalyzer(structure)
        symm_structure = SGA.get_symmetrized_structure()
        equivalent_sites = symm_structure.as_dict()['equivalent_positions']
        unique_site_indices, site_counts = np.unique(equivalent_sites, return_counts=True)
        periodic_site_list = []
        for ind in unique_site_indices:
            periodic_site_list.append(symm_structure.sites[ind])

        unique_site_dict = {}
        for i in range(len(periodic_site_list)):
            unique_site_dict[periodic_site_list[i]] = {}
            unique_site_dict[periodic_site_list[i]]['Index'] = unique_site_indices[i]
            unique_site_dict[periodic_site_list[i]]['Equivalent Sites'] = site_counts[i]
        return unique_site_dict

    def alter_structures(self):
        if self.calculation_dict['Type'] == 'bulk':
            for structure in self.calculation_structures_dict.keys():
                for magnetism in self.calculation_structures_dict[structure].keys():
                    base_structure = self.calculation_structures_dict[structure][magnetism]
                    bulk_dict = {}
                    bulk_key = str(base_structure.formula)
                    bulk_dict[bulk_key] = base_structure
                    self.calculation_structures_dict[structure][magnetism] = bulk_dict

        elif self.calculation_dict['Type'] == 'defect':
            self.unique_defect_sites = {}
            defect_element = self.calculation_dict['Defect']
            for structure in self.calculation_structures_dict.keys():
                for magnetism in self.calculation_structures_dict[structure].keys():
                    base_structure = self.calculation_structures_dict[structure][magnetism]
                    rescaled_structure = self.structure_rescaler(base_structure)
                    unique_site_dict = self.get_unique_sites(rescaled_structure)

                    defect_dict = {}
                    # defect_key = str(defect_element) + ' Defect '
                    defect_number = 1
                    unique_defects_dict = {}
                    for periodic_site in unique_site_dict.keys():
                        defect_structure = copy.deepcopy(rescaled_structure)
                        if Element(periodic_site.as_dict()['species'][0]['element']) == Element(defect_element):
                            unique_defects_dict[periodic_site] = unique_site_dict[periodic_site]
                            defect_structure.remove_sites([unique_site_dict[periodic_site]['Index']])
                            defect_key = str(defect_structure.formula)
                            defect_dict[defect_key + ' ' + str(defect_number)] = defect_structure
                            unique_defects_dict[periodic_site]['Run Directory Name'] = str(defect_key + ' ' + str(defect_number)).replace(' ', '_')
                            defect_number += 1
                        else:
                            continue
                    self.calculation_structures_dict[structure][magnetism] = defect_dict
                    if unique_defects_dict != {}:
                        self.unique_defect_sites[structure] = unique_defects_dict

        else:
            print('Calculation Type %s not recognized; fatal error' % self.calculation_dict['Type'])
            sys.exit(1)


class WriteVaspFiles:
    def __init__(self, calculation_structures_dict, calculation_dict, incar_tags, relaxation_set):
        self.calculation_structures_dict = calculation_structures_dict
        self.calculation_dict = calculation_dict
        self.incar_tags = incar_tags
        self.relaxation_set = relaxation_set

        self.write_vasp_poscar()

    def check_directory_existence(self, directory):
        try:
            os.mkdir(directory)
        except FileExistsError:
            pass

    def write_vasp_poscar(self):
        if self.calculation_dict['Type'] == 'bulk':
            calc = 'bulk'
        elif self.calculation_dict['Type'] == 'defect':
            calc = str(self.calculation_dict['Defect']) + ' defect'

        top_level_dirname = self.calculation_dict['Type']
        self.check_directory_existence(top_level_dirname)
        for structure in self.calculation_structures_dict.keys():
            structure_dirname = structure.replace(' ', '_')
            structure_dir_path = os.path.join(top_level_dirname, structure_dirname)
            for magnetism in self.calculation_structures_dict[structure].keys():
                magnetism_dirname = magnetism.replace(' ', '_')
                magnetism_dir_path = os.path.join(structure_dir_path, magnetism_dirname)
                if not bool(self.calculation_structures_dict[structure][magnetism]) == True:
                    # empty dictionary check; sometimes occurs with defect calcs
                    print('%s not compatible with %s calculation' % (structure, calc))
                    continue
                else:
                    for calculation_type in self.calculation_structures_dict[structure][magnetism].keys():
                        calculation_type_dirname = calculation_type.replace(' ', '_')
                        calculation_type_path = os.path.join(magnetism_dir_path, calculation_type_dirname)
                        write_structure = self.calculation_structures_dict[structure][magnetism][calculation_type]
                        if type(write_structure) == Structure:
                            self.check_directory_existence(structure_dir_path)
                            self.check_directory_existence(magnetism_dir_path)
                            self.check_directory_existence(calculation_type_path)
                            structure_path = os.path.join(calculation_type_path, "POSCAR")
                            write_structure.to(filename=structure_path)
                        else:
                            print('Not valid structure type')
                            continue
