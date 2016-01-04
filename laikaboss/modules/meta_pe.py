# Copyright 2015 Lockheed Martin Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import re
import struct
import hashlib
import binascii
import logging
import pefile
from datetime import datetime
from laikaboss.objectmodel import (ModuleObject,
                                   ExternalVars,
                                   QuitScanException,
                                   GlobalScanTimeoutError,
                                   GlobalModuleTimeoutError)
from laikaboss.si_module import SI_MODULE

IMAGE_MAGIC_LOOKUP = {0x10b: '32_BIT',
                      0x20b: '64_BIT',
                      0x107: 'ROM_IMAGE', }

class META_PE(SI_MODULE):
    def __init__(self):
        self.module_name = "META_PE"

    def _run(self, scanObject, result, depth, args):
        moduleResult = []
        imports = {}
        sections = {}
        exports = []
        imageMagic = ''

        try:
            pe = pefile.PE(data=scanObject.buffer)
            dump_dict = pe.dump_dict()

            for section in dump_dict.get('PE Sections', []):
                secName = section.get('Name', {}).get('Value', '').strip('\0')
                ptr = section.get('PointerToRawData', {}).get('Value')
                virtAddress = section.get('VirtualAddress', {}).get('Value')
                virtSize = section.get('Misc_VirtualSize', {}).get('Value')
                size = section.get('SizeOfRawData', {}).get('Value')
                secData = pe.get_data(ptr, size)
                secInfo = {'Virtual Address': '0x%08X' % virtAddress,
                           'Virtual Size': virtSize,
                           'Raw Size': size,
                           'MD5': section.get('MD5', ''),
                           'SHA1': section.get('SHA1', ''),
                           'SHA256': section.get('SHA256', ''),
                           'Entropy': section.get('Entropy', ''),
                           'Section Characteristics': section.get('Flags', []),
                           'Structure': section.get('Structure', ''), }
                if secInfo['MD5'] != scanObject.objectHash:
                    moduleResult.append(ModuleObject(
                        buffer=secData,
                        externalVars=ExternalVars(filename=secName)))
                sections[secName] = secInfo
            sections['Total'] = pe.FILE_HEADER.NumberOfSections
            scanObject.addMetadata(self.module_name, 'Sections', sections)

            try:
                for exp in pe.DIRECTORY_ENTRY_EXPORT.symbols:
                    exports.append(exp.name)
                scanObject.addMetadata(self.module_name, 'Exports', exports)
            except (QuitScanException,
                    GlobalScanTimeoutError,
                    GlobalModuleTimeoutError):
                raise
            except:
                logging.debug('No export entries')

            try:
                scanObject.addMetadata(self.module_name,
                                       'Imphash', pe.get_imphash())
            except:
                logging.debug('Unable to identify imphash')

            for imp_symbol in dump_dict['Imported symbols']:
                for imp in imp_symbol:
                    if imp.get('DLL'):
                        dll = imp.get('DLL')
                        imports.setdefault(dll, [])
                        # Imports can be identified by ordinal or name
                        if imp.get('Ordinal'):
                            ordinal = imp.get('Ordinal')
                            imports[dll].append(ordinal)
                        if imp.get('Name'):
                            name = imp.get('Name')
                            imports[dll].append(name)
            scanObject.addMetadata(self.module_name, 'Imports', imports)

            imgChars = dump_dict.get('Flags', [])
            scanObject.addMetadata(
                self.module_name, 'Image Characteristics', imgChars)
            # Make a pretty date format
            date = datetime.fromtimestamp(pe.FILE_HEADER.TimeDateStamp)
            isoDate = date.isoformat()
            scanObject.addMetadata(self.module_name, 'Date', isoDate)
            scanObject.addMetadata(
                self.module_name, 'Timestamp', pe.FILE_HEADER.TimeDateStamp)

            machine = pe.FILE_HEADER.Machine
            machineData = {
                'Id': machine,
                'Type': pefile.MACHINE_TYPE.get(machine)
            }
            scanObject.addMetadata(
                self.module_name, 'Machine Type', machineData)

            # Reference: http://msdn.microsoft.com/en-us/library/windows/desktop/ms680339%28v=vs.85%29.aspx
            scanObject.addMetadata(
                self.module_name,
                'Image Magic',
                IMAGE_MAGIC_LOOKUP.get(imageMagic, 'Unknown'))

            dllChars = dump_dict.get('DllCharacteristics', [])
            scanObject.addMetadata(
                self.module_name, 'DLL Characteristics', dllChars)

            subsystem = pe.OPTIONAL_HEADER.Subsystem
            subName = pefile.SUBSYSTEM_TYPE.get(subsystem)
            scanObject.addMetadata(self.module_name, 'Subsystem', subName)

            # Reference: http://msdn.microsoft.com/en-us/library/windows/desktop/ms648009%28v=vs.85%29.aspx

            try:
                for resource in pe.DIRECTORY_ENTRY_RESOURCE.entries:
                    res_type = pefile.RESOURCE_TYPE.get(resource.id, 'Unknown')
                    for entry in resource.directory.entries:
                        for e_entry in entry.directory.entries:
                            sublang = pefile.get_sublang_name_for_lang(
                                e_entry.data.lang,
                                e_entry.data.sublang,
                            )
                            offset = e_entry.data.struct.OffsetToData
                            size = e_entry.data.struct.Size
                            raw_data = pe.get_data(offset, size)
                            language = pefile.LANG.get(
                                e_entry.data.lang, 'Unknown')
                            data = {
                                'Type': res_type,
                                'Id': e_entry.id,
                                'Name': e_entry.data.struct.name,
                                'Offset': offset,
                                'Size': size,
                                'SHA256': hashlib.sha256(raw_data).hexdigest(),
                                'SHA1': hashlib.sha1(raw_data).hexdigest(),
                                'MD5': hashlib.md5(raw_data).hexdigest(),
                                'Language': language,
                                'Sub Language': sublang,
                            }
                            scanObject.addMetadata(
                                self.module_name, 'Resources', data)
            except (QuitScanException,
                    GlobalScanTimeoutError,
                    GlobalModuleTimeoutError):
                raise
            except:
                logging.debug('No resources')

            scanObject.addMetadata(
                self.module_name,
                'Stack Reserve Size',
                pe.OPTIONAL_HEADER.SizeOfStackReserve)
            scanObject.addMetadata(
                self.module_name,
                'Stack Commit Size',
                pe.OPTIONAL_HEADER.SizeOfStackCommit)
            scanObject.addMetadata(
                self.module_name,
                'Heap Reserve Size',
                pe.OPTIONAL_HEADER.SizeOfHeapReserve)
            scanObject.addMetadata(
                self.module_name,
                'Heap Commit Size',
                pe.OPTIONAL_HEADER.SizeOfHeapCommit)
            scanObject.addMetadata(
                self.module_name,
                'EntryPoint',
                hex(pe.OPTIONAL_HEADER.AddressOfEntryPoint))
            scanObject.addMetadata(
                self.module_name,
                'ImageBase',
                hex(pe.OPTIONAL_HEADER.ImageBase))

            # Parse RSDS & Rich
            scanObject.addMetadata(
                self.module_name, 'RSDS', self.parseRSDS(scanObject))
            scanObject.addMetadata(
                self.module_name, 'Rich Header', self.parseRich(pe))

        except pefile.PEFormatError:
            logging.debug("Invalid PE format")
        return moduleResult

    @staticmethod
    def parseRSDS(scanObject):
        """
        Parses out RSDS pdb information

        00000000  52 53 44 53 b4 bc 76 74  d2 9f 6a 49 b5 6c 74 7c  |RSDS..vt..jI.lt||
        00000010  1d 41 bb a5 05 00 00 00  44 3a 5c 4d 69 63 72 6f  |.A......D:\Micro|
        00000020  73 6f 66 74 20 56 69 73  75 61 6c 20 53 74 75 64  |soft Visual Stud|
        00000030  69 6f 5c 66 69 6c 65 73  5c 43 23 5c 7a 63 67 2e  |io\files\C#\zcg.|
        00000040  43 68 6f 70 70 65 72 53  72 65 76 65 72 46 6f 72  |ChopperSreverFor|
        00000050  43 73 68 61 72 70 5c 6f  62 6a 5c 52 65 6c 65 61  |Csharp\obj\Relea|
        00000060  73 65 5c 53 79 73 74 65  6d 2e 57 65 62 53 65 72  |se\System.WebSer|
        00000070  76 69 63 65 73 2e 70 64  62 00 00 00 04 55 00 00  |vices.pdb....U..|

        +0h   dword        "RSDS" signature
        +4h   GUID         16-byte Globally Unique Identifier
        +14h  dword        "age"
        +18h  byte string  zero terminated UTF8 path and file name

        http://www.godevtool.com/Other/pdb.htm
        """

        result = {}
        rsds = re.compile('RSDS.{24,1024}\.pdb')
        x = rsds.findall(scanObject.buffer)

        if x and x[-1]:
            match = x[-1]
            result["guid"] = "%s-%s-%s-%s" % (binascii.hexlify(match[4:8]),
                                              binascii.hexlify(match[8:10]),
                                              binascii.hexlify(match[10:12]),
                                              binascii.hexlify(match[12:20]))
            result["age"] = struct.unpack('<L', match[20:24])[0]
            result["pdb"] = match[24:]

        return result

    @staticmethod
    def parseRich(pe):
        """
        Parses out Rich header information using pefile.
        """
        result = {}
        data = []
        if pe.RICH_HEADER:
            for x in range(0, len(pe.RICH_HEADER.values), 2):
                value = pe.RICH_HEADER.values[x] >> 16
                version = pe.RICH_HEADER.values[x] & 0xffff
                count = pe.RICH_HEADER.values[x + 1]
                data.append({'Id': value,
                             'Version': version,
                             'Count': count, })

            result['Rich Header Values'] = data
            result['Checksum'] = pe.RICH_HEADER.checksum

        return result
