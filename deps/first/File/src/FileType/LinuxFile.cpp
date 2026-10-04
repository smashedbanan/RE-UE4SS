// Linux implementation of the File interface, mirroring WinFile.cpp function by function (POSIX
// open/write/mmap/stat instead of CreateFileW/WriteFile/MapViewOfFile/GetFileInformationByHandle).
// Without it the Output system cannot write files at all (UE4SS.log, FileDevice/NewFileDevice).
#ifdef __linux__
#include <cerrno>
#include <cstring>
#include <fstream>

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <File/File.hpp>
#include <File/FileType/LinuxFile.hpp>
#include <File/HandleTemplate.hpp>

#include <Helpers/String.hpp>
#include <fmt/core.h>

namespace RC::File
{
    namespace
    {
        // The interface stores the OS handle as a void*: the file descriptor travels inside it.
        auto to_fd(void* handle) -> int
        {
            return static_cast<int>(reinterpret_cast<intptr_t>(handle));
        }

        auto to_handle(int fd) -> void*
        {
            return reinterpret_cast<void*>(static_cast<intptr_t>(fd));
        }

        auto errno_text() -> std::string
        {
            return std::strerror(errno);
        }

        // stat() fields mapped onto the same nine identifying slots WinFile serializes.
        auto identity_of(int fd, unsigned long (&out)[9]) -> bool
        {
            struct stat info{};
            if (fstat(fd, &info) != 0)
            {
                return false;
            }
            out[0] = static_cast<unsigned long>(info.st_dev);
            out[1] = static_cast<unsigned long>(info.st_ino & 0xFFFFFFFFu);
            out[2] = static_cast<unsigned long>(static_cast<uint64_t>(info.st_ino) >> 32);
            out[3] = static_cast<unsigned long>(info.st_ctim.tv_sec);
            out[4] = static_cast<unsigned long>(info.st_ctim.tv_nsec);
            out[5] = static_cast<unsigned long>(info.st_mtim.tv_sec);
            out[6] = static_cast<unsigned long>(info.st_mtim.tv_nsec);
            out[7] = static_cast<unsigned long>(static_cast<uint64_t>(info.st_size) & 0xFFFFFFFFu);
            out[8] = static_cast<unsigned long>(static_cast<uint64_t>(info.st_size) >> 32);
            return true;
        }
    } // namespace

    auto LinuxFile::is_valid() noexcept -> bool
    {
        return m_file != nullptr;
    }

    auto LinuxFile::invalidate_file() noexcept -> void
    {
        m_file = nullptr;
        m_map_handle = nullptr;
        m_memory_map = nullptr;
    }

    auto LinuxFile::delete_file(const std::filesystem::path& file_path_and_name) -> void
    {
        if (unlink(file_path_and_name.c_str()) != 0)
        {
            THROW_INTERNAL_FILE_ERROR(fmt::format("[LinuxFile::delete_file] Was unable to delete file, error: {}", errno_text()))
        }
    }

    auto LinuxFile::delete_file() -> void
    {
        if (m_is_file_open)
        {
            close_file();
        }

        delete_file(m_file_path_and_name);
    }

    auto LinuxFile::set_file(HANDLE new_file) -> void
    {
        m_file = new_file;
    }

    auto LinuxFile::get_file() -> HANDLE
    {
        return m_file;
    }

    auto LinuxFile::set_is_file_open(bool new_is_open) -> void
    {
        m_is_file_open = new_is_open;
    }

    auto LinuxFile::get_raw_handle() noexcept -> void*
    {
        return m_file;
    }

    auto LinuxFile::get_file_path() const noexcept -> const std::filesystem::path&
    {
        return m_file_path_and_name;
    }

    auto LinuxFile::set_serialization_output_file(const std::filesystem::path& output_file) noexcept -> void
    {
        m_serialization_file_path_and_name = output_file;
    }

    auto LinuxFile::serialization_file_exists() -> bool
    {
        return std::filesystem::exists(m_serialization_file_path_and_name);
    }

    template <typename DataType>
    auto write_to_file(LinuxFile& file, DataType* data, size_t num_bytes_to_write) -> void
    {
        if (!file.is_file_open())
        {
            THROW_INTERNAL_FILE_ERROR("[LinuxFile::write_to_file] Tried writing to file but the file is not open")
        }

        // write() may write less than asked (signals, pipes): loop until everything is out.
        auto bytes = reinterpret_cast<const char*>(data);
        while (num_bytes_to_write > 0)
        {
            ssize_t written = write(to_fd(file.get_file()), bytes, num_bytes_to_write);
            if (written < 0)
            {
                if (errno == EINTR)
                {
                    continue;
                }
                THROW_INTERNAL_FILE_ERROR(fmt::format("[LinuxFile::write_to_file] Tried writing to file but was unable to complete operation. error: {}",
                                                      errno_text()))
            }
            bytes += written;
            num_bytes_to_write -= static_cast<size_t>(written);
        }
    }

    // Serialization Format (Linux): the same nine slots as WinFile, filled from stat():
    // st_dev, st_ino (low, high), st_ctim (sec, nsec), st_mtim (sec, nsec), st_size (low, high), user_data
    auto LinuxFile::serialize_identifying_properties() -> void
    {
        if (m_serialization_file_path_and_name.empty())
        {
            THROW_INTERNAL_FILE_ERROR("[LinuxFile::serialize_identifying_properties]: Path & file name for serialization file is empty, please call "
                                      "'set_serialization_output_file'")
        }

        unsigned long identity[9]{};
        identity_of(to_fd(m_file), identity);
        for (unsigned long value : identity)
        {
            serialize_item(GenericItemData{.data_type = GenericDataType::UnsignedLong, .data_ulong = value}, true);
        }
    }

    auto LinuxFile::deserialize_identifying_properties() -> void
    {
        m_identifying_properties.volume_serial_number = *static_cast<unsigned long*>(get_serialized_item(sizeof(unsigned long), true));
        m_identifying_properties.file_index_low = *static_cast<unsigned long*>(get_serialized_item(sizeof(unsigned long), true));
        m_identifying_properties.file_index_high = *static_cast<unsigned long*>(get_serialized_item(sizeof(unsigned long), true));
        m_identifying_properties.creation_time_low = *static_cast<unsigned long*>(get_serialized_item(sizeof(unsigned long), true));
        m_identifying_properties.creation_time_high = *static_cast<unsigned long*>(get_serialized_item(sizeof(unsigned long), true));
        m_identifying_properties.last_write_time_low = *static_cast<unsigned long*>(get_serialized_item(sizeof(unsigned long), true));
        m_identifying_properties.last_write_time_high = *static_cast<unsigned long*>(get_serialized_item(sizeof(unsigned long), true));
        m_identifying_properties.file_size_low = *static_cast<unsigned long*>(get_serialized_item(sizeof(unsigned long), true));
        m_identifying_properties.file_size_high = *static_cast<unsigned long*>(get_serialized_item(sizeof(unsigned long), true));

        // The cached identifying properties are not for user-code: the next item handed out is the
        // first one after them.
        m_offset_to_next_serialized_item = sizeof(IdentifyingProperties);
        m_has_cached_identifying_properties = true;
    }

    auto LinuxFile::is_deserialized_and_live_equal() -> bool
    {
        if (!m_has_cached_identifying_properties)
        {
            if (!std::filesystem::exists(m_serialization_file_path_and_name))
            {
                return false;
            }

            deserialize_identifying_properties();
        }

        unsigned long live[9]{};
        if (!identity_of(to_fd(m_file), live))
        {
            return false;
        }
        const auto& cached = m_identifying_properties;
        return live[0] == cached.volume_serial_number && live[1] == cached.file_index_low && live[2] == cached.file_index_high &&
               live[3] == cached.creation_time_low && live[4] == cached.creation_time_high && live[5] == cached.last_write_time_low &&
               live[6] == cached.last_write_time_high && live[7] == cached.file_size_low && live[8] == cached.file_size_high;
    }

    auto LinuxFile::invalidate_serialization() -> void
    {
        if (m_serialization_file_path_and_name.empty())
        {
            THROW_INTERNAL_FILE_ERROR("[LinuxFile::invalidate_serialization] Tried to invalidate serialization but "
                                      "'m_serialization_file_path_and_name' was empty, please call 'set_serialization_output_file'")
        }

        if (std::filesystem::exists(m_serialization_file_path_and_name))
        {
            delete_file(m_serialization_file_path_and_name);
        }
    }

    template <typename DataType>
    auto serialize_typed_item(DataType data, Handle& output_file) -> void
    {
        write_to_file(output_file.get_underlying_type(), &data, sizeof(DataType));
    }

    auto LinuxFile::serialize_item(const GenericItemData& data, bool is_internal_item) -> void
    {
        if (m_serialization_file_path_and_name.empty())
        {
            THROW_INTERNAL_FILE_ERROR(
                    "[LinuxFile::serialize_item]: Path & file name for serialization file is empty, please call 'set_serialization_output_file'")
        }

        if (!serialization_file_exists() && !is_internal_item)
        {
            // Same rule as WinFile: a new cache file starts with the identifying properties.
            serialize_identifying_properties();
        }

        Handle serialization_file = open(m_serialization_file_path_and_name, OpenFor::Appending, OverwriteExistingFile::No, CreateIfNonExistent::Yes);

        switch (data.data_type)
        {
        case GenericDataType::UnsignedLong:
            serialize_typed_item<unsigned long>(data.data_ulong, serialization_file);
            serialization_file.get_underlying_type().m_offset_to_next_serialized_item += sizeof(unsigned long);
            break;
        case GenericDataType::SignedLong:
            serialize_typed_item<signed long>(data.data_long, serialization_file);
            serialization_file.get_underlying_type().m_offset_to_next_serialized_item += sizeof(signed long);
            break;
        case GenericDataType::UnsignedLongLong:
            serialize_typed_item<unsigned long long>(data.data_ulonglong, serialization_file);
            serialization_file.get_underlying_type().m_offset_to_next_serialized_item += sizeof(unsigned long long);
            break;
        case GenericDataType::SignedLongLong:
            serialize_typed_item<signed long long>(data.data_longlong, serialization_file);
            serialization_file.get_underlying_type().m_offset_to_next_serialized_item += sizeof(signed long long);
            break;
        }

        serialization_file.close();
    }

    auto LinuxFile::get_serialized_item(size_t data_size, bool is_internal_item) -> void*
    {
        if (!m_has_cache_in_memory)
        {
            if (m_serialization_file_path_and_name.empty())
            {
                THROW_INTERNAL_FILE_ERROR(
                        "[LinuxFile::get_serialized_item]: Path & file name for serialization file is empty, please call 'set_serialization_output_file'")
            }

            Handle cache_file = open(m_serialization_file_path_and_name);
            if (read(to_fd(cache_file.get_raw_handle()), &m_cache, cache_size) < 0)
            {
                THROW_INTERNAL_FILE_ERROR(fmt::format("[LinuxFile::get_serialized_item] Tried deserializing file but was unable to complete operation. error: {}",
                                                      errno_text()))
            }
            cache_file.close();

            m_has_cache_in_memory = true;
        }

        if (!m_has_cached_identifying_properties && !is_internal_item)
        {
            deserialize_identifying_properties();
        }

        void* data_ptr = &m_cache[m_offset_to_next_serialized_item];
        m_offset_to_next_serialized_item += data_size;
        return data_ptr;
    }

    auto LinuxFile::close_current_file() -> void
    {
        close_file();
    }

    auto LinuxFile::create_all_directories(const std::filesystem::path& file_name_and_path) -> void
    {
        if (file_name_and_path.parent_path().empty())
        {
            return;
        }

        try
        {
            std::filesystem::create_directories(file_name_and_path.parent_path());
        }
        catch (const std::filesystem::filesystem_error& e)
        {
            THROW_INTERNAL_FILE_ERROR(fmt::format("[LinuxFile::create_all_directories] Tried creating directories '{}' but encountered an error. error: {}",
                                                  file_name_and_path.string(),
                                                  e.what()))
        }
    }

    auto LinuxFile::close_file() -> void
    {
        if (m_memory_map)
        {
            // m_map_handle carries the mapped length (there is no separate mapping object on Linux).
            if (munmap(m_memory_map, reinterpret_cast<size_t>(m_map_handle)) != 0)
            {
                THROW_INTERNAL_FILE_ERROR(fmt::format("[LinuxFile::close_file] Was unable to unmap file, error: {}", errno_text()))
            }
            m_memory_map = nullptr;
            m_map_handle = nullptr;
        }

        if (!is_valid() || !is_file_open())
        {
            return;
        }

        if (close(to_fd(m_file)) != 0)
        {
            THROW_INTERNAL_FILE_ERROR(fmt::format("[LinuxFile::close_file] Was unable to close file, {}", errno_text()))
        }
        else
        {
            set_is_file_open(false);
        }
    }

    auto LinuxFile::is_file_open() const -> bool
    {
        return m_is_file_open;
    }

    auto LinuxFile::write_string_to_file(StringViewType string_to_write) -> void
    {
        // Same as WinFile: the UE4SS string (UTF-16 here) goes to disk as UTF-8.
        std::string string_converted_to_utf8 = to_string(string_to_write);
        write_to_file(*this, string_converted_to_utf8.c_str(), string_converted_to_utf8.size());
    }

    auto LinuxFile::is_same_as(LinuxFile& other_file) -> bool
    {
        unsigned long mine[9]{}, theirs[9]{};
        if (!identity_of(to_fd(m_file), mine) || !identity_of(to_fd(other_file.get_file()), theirs))
        {
            THROW_INTERNAL_FILE_ERROR(fmt::format("[LinuxFile::is_same_as] Tried retrieving file information. {}", errno_text()))
        }
        return std::equal(std::begin(mine), std::end(mine), std::begin(theirs));
    }

    auto LinuxFile::read_all() const -> StringType
    {
        std::ifstream stream{get_file_path(), std::ios::in | std::ios::binary};
        if (!stream)
        {
            THROW_INTERNAL_FILE_ERROR(fmt::format("[LinuxFile::read_all] Tried to read entire file but returned error {}", errno))
        }

        std::string bytes{std::istreambuf_iterator<char>{stream}, std::istreambuf_iterator<char>{}};
        size_t start = 0;
        if (bytes.size() >= 3 && static_cast<unsigned char>(bytes[0]) == 0xEF && static_cast<unsigned char>(bytes[1]) == 0xBB &&
            static_cast<unsigned char>(bytes[2]) == 0xBF)
        {
            // BOM: UTF-8
            start = 3;
        }

        // Like WinFile (a wide stream in the default locale), every byte becomes one character: a
        // config or script file is read the same way on both platforms.
        StringType file_contents;
        file_contents.reserve(bytes.size() - start);
        for (size_t index = start; index < bytes.size(); ++index)
        {
            file_contents.push_back(static_cast<CharType>(static_cast<unsigned char>(bytes[index])));
        }
        return file_contents;
    }

    auto LinuxFile::memory_map() -> std::span<uint8_t>
    {
        int protection;
        switch (m_open_properties.open_for)
        {
        case OpenFor::Writing:
        case OpenFor::Appending:
        case OpenFor::ReadWrite:
            protection = PROT_READ | PROT_WRITE;
            break;
        case OpenFor::Reading:
            protection = PROT_READ;
            break;
        default:
            THROW_INTERNAL_FILE_ERROR("[LinuxFile::memory_map] Tried to memory map file but 'm_open_properties' contains invalid data.")
        }

        struct stat info{};
        if (fstat(to_fd(m_file), &info) != 0)
        {
            THROW_INTERNAL_FILE_ERROR(fmt::format("[LinuxFile::memory_map] Tried to memory map file but 'fstat' returned {}", errno_text()))
        }

        const auto size = static_cast<size_t>(info.st_size);
        void* mapping = mmap(nullptr, size, protection, MAP_SHARED, to_fd(m_file), 0);
        if (mapping == MAP_FAILED)
        {
            THROW_INTERNAL_FILE_ERROR(fmt::format("[LinuxFile::memory_map] Tried to memory map file but 'mmap' returned {}", errno_text()))
        }

        m_memory_map = static_cast<uint8_t*>(mapping);
        m_map_handle = reinterpret_cast<HANDLE>(size);
        return std::span(m_memory_map, size);
    }

    auto LinuxFile::open_file(const std::filesystem::path& file_name_and_path, const OpenProperties& open_properties) -> LinuxFile
    {
        if (file_name_and_path.empty())
        {
            THROW_INTERNAL_FILE_ERROR("[LinuxFile::open_file] Tried to open file but file_name_and_path was empty.")
        }

        int flags;
        switch (open_properties.open_for)
        {
        case OpenFor::Writing:
            flags = O_WRONLY;
            break;
        case OpenFor::Appending:
            flags = O_WRONLY | O_APPEND;
            break;
        case OpenFor::Reading:
            flags = O_RDONLY;
            break;
        case OpenFor::ReadWrite:
            flags = O_RDWR;
            break;
        default:
            THROW_INTERNAL_FILE_ERROR("[LinuxFile::open_file] Tried to open file but received invalid data for the 'OpenFor' parameter.")
        }

        // CREATE_ALWAYS -> create + truncate, OPEN_ALWAYS -> create, OPEN_EXISTING -> neither.
        if (open_properties.overwrite_existing_file == OverwriteExistingFile::Yes)
        {
            create_all_directories(file_name_and_path);
            flags |= O_CREAT | O_TRUNC;
        }
        else if (open_properties.create_if_non_existent == CreateIfNonExistent::Yes)
        {
            create_all_directories(file_name_and_path);
            flags |= O_CREAT;
        }

        LinuxFile file{};
        const int fd = ::open(file_name_and_path.c_str(), flags | O_CLOEXEC, 0644);
        if (fd < 0)
        {
            std::string_view open_type = open_properties.open_for == OpenFor::Writing || open_properties.open_for == OpenFor::Appending ? "writing" : "reading";

            THROW_INTERNAL_FILE_ERROR(fmt::format("[LinuxFile::open_file] Tried opening file for {} but encountered an error. Path & File: {} | error: {}\n",
                                                  open_type,
                                                  file_name_and_path.string(),
                                                  errno_text()))
        }

        file.set_file(to_handle(fd));
        file.m_file_path_and_name = file_name_and_path;
        file.set_is_file_open(true);
        file.m_open_properties = open_properties;

        return file;
    }
} // namespace RC::File

#endif // ifdef __linux__
