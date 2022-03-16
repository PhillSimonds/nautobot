import types
from unittest import skip
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.test.client import RequestFactory
from django.urls import reverse
from graphql import GraphQLError
import graphene.types
from graphene_django import DjangoObjectType
from graphene_django.settings import graphene_settings
from graphql.error.located_error import GraphQLLocatedError
from graphql import get_default_backend
from rest_framework import status
from rest_framework.test import APIClient

from nautobot.circuits.models import Provider, CircuitTermination
from nautobot.core.graphql.generators import (
    generate_list_search_parameters,
    generate_schema_type,
)
from nautobot.core.graphql import execute_query, execute_saved_query
from nautobot.core.graphql.utils import str_to_var_name
from nautobot.core.graphql.schema import (
    extend_schema_type,
    extend_schema_type_custom_field,
    extend_schema_type_tags,
    extend_schema_type_config_context,
    extend_schema_type_relationships,
    extend_schema_type_null_field_choice,
)
from nautobot.dcim.choices import InterfaceTypeChoices, InterfaceModeChoices, PortTypeChoices
from nautobot.dcim.filters import DeviceFilterSet, SiteFilterSet
from nautobot.dcim.graphql.types import DeviceType as DeviceTypeGraphQL
from nautobot.dcim.models import (
    Cable,
    Device,
    DeviceRole,
    DeviceType,
    FrontPort,
    Interface,
    Manufacturer,
    Rack,
    RearPort,
    Region,
    Site,
)
from nautobot.extras.choices import CustomFieldTypeChoices
from nautobot.utilities.testing.utils import create_test_user

from nautobot.extras.models import (
    ChangeLoggedModel,
    CustomField,
    ConfigContext,
    GraphQLQuery,
    Relationship,
    RelationshipAssociation,
    Status,
    Webhook,
)
from nautobot.ipam.models import IPAddress, VLAN
from nautobot.users.models import ObjectPermission, Token
from nautobot.tenancy.models import Tenant
from nautobot.virtualization.models import Cluster, ClusterType, VirtualMachine, VMInterface

# Use the proper swappable User model
User = get_user_model()


class GraphQLTestCase(TestCase):
    def setUp(self):
        self.user = create_test_user("graphql_testuser")
        GraphQLQuery.objects.create(name="GQL 1", slug="gql-1", query="{ query: sites {name} }")
        GraphQLQuery.objects.create(
            name="GQL 2", slug="gql-2", query="query ($name: [String!]) { sites(name:$name) {name} }"
        )
        self.region = Region.objects.create(name="Region")
        self.sites = (
            Site.objects.create(name="Site-1", slug="site-1", region=self.region),
            Site.objects.create(name="Site-2", slug="site-2", region=self.region),
            Site.objects.create(name="Site-3", slug="site-3", region=self.region),
        )

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_execute_query(self):
        query = "{ query: sites {name} }"
        resp = execute_query(query, user=self.user).to_dict()
        self.assertFalse(resp["data"].get("error"))
        self.assertEquals(len(resp["data"]["query"]), 3)

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_execute_query_with_variable(self):
        query = "query ($name: [String!]) { sites(name:$name) {name} }"
        resp = execute_query(query, user=self.user, variables={"name": "Site-1"}).to_dict()
        self.assertFalse(resp.get("error"))
        self.assertEquals(len(resp["data"]["sites"]), 1)

    def test_execute_query_with_error(self):
        query = "THIS TEST WILL ERROR"
        with self.assertRaises(GraphQLError):
            execute_query(query, user=self.user).to_dict()

    def test_execute_saved_query(self):
        resp = execute_saved_query("gql-1", user=self.user).to_dict()
        self.assertFalse(resp["data"].get("error"))

    def test_execute_saved_query_with_variable(self):
        resp = execute_saved_query("gql-2", user=self.user, variables={"name": "site-1"}).to_dict()
        self.assertFalse(resp["data"].get("error"))


class GraphQLUtilsTestCase(TestCase):
    def test_str_to_var_name(self):

        self.assertEqual(str_to_var_name("IP Addresses"), "ip_addresses")
        self.assertEqual(str_to_var_name("My New VAR"), "my_new_var")
        self.assertEqual(str_to_var_name("My-VAR"), "my_var")


class GraphQLGenerateSchemaTypeTestCase(TestCase):
    def test_model_w_filterset(self):

        schema = generate_schema_type(app_name="dcim", model=Device)
        self.assertEqual(schema.__bases__[0], DjangoObjectType)
        self.assertEqual(schema._meta.model, Device)
        self.assertEqual(schema._meta.filterset_class, DeviceFilterSet)

    def test_model_wo_filterset(self):

        schema = generate_schema_type(app_name="wrong_app", model=ChangeLoggedModel)
        self.assertEqual(schema.__bases__[0], DjangoObjectType)
        self.assertEqual(schema._meta.model, ChangeLoggedModel)
        self.assertIsNone(schema._meta.filterset_class)


class GraphQLExtendSchemaType(TestCase):
    def setUp(self):

        self.datas = (
            {"field_name": "my_text", "field_type": CustomFieldTypeChoices.TYPE_TEXT},
            {
                "field_name": "my new field",
                "field_type": CustomFieldTypeChoices.TYPE_TEXT,
            },
            {
                "field_name": "my_int1",
                "field_type": CustomFieldTypeChoices.TYPE_INTEGER,
            },
            {
                "field_name": "my_int2",
                "field_type": CustomFieldTypeChoices.TYPE_INTEGER,
            },
            {
                "field_name": "my_bool_t",
                "field_type": CustomFieldTypeChoices.TYPE_BOOLEAN,
            },
            {
                "field_name": "my_bool_f",
                "field_type": CustomFieldTypeChoices.TYPE_BOOLEAN,
            },
            {"field_name": "my_date", "field_type": CustomFieldTypeChoices.TYPE_DATE},
            {"field_name": "my_url", "field_type": CustomFieldTypeChoices.TYPE_URL},
        )

        obj_type = ContentType.objects.get_for_model(Site)

        # Create custom fields for Site objects
        for data in self.datas:
            cf = CustomField.objects.create(type=data["field_type"], name=data["field_name"], required=False)
            cf.content_types.set([obj_type])

        self.schema = generate_schema_type(app_name="dcim", model=Site)

    @override_settings(GRAPHQL_CUSTOM_FIELD_PREFIX="pr")
    def test_extend_custom_field_w_prefix(self):

        schema = extend_schema_type_custom_field(self.schema, Site)

        for data in self.datas:
            field_name = f"pr_{str_to_var_name(data['field_name'])}"
            self.assertIn(field_name, schema._meta.fields.keys())

    @override_settings(GRAPHQL_CUSTOM_FIELD_PREFIX="")
    def test_extend_custom_field_wo_prefix(self):

        schema = extend_schema_type_custom_field(self.schema, Site)

        for data in self.datas:
            field_name = str_to_var_name(data["field_name"])
            self.assertIn(field_name, schema._meta.fields.keys())

    @override_settings(GRAPHQL_CUSTOM_FIELD_PREFIX=None)
    def test_extend_custom_field_prefix_none(self):

        schema = extend_schema_type_custom_field(self.schema, Site)

        for data in self.datas:
            field_name = str_to_var_name(data["field_name"])
            self.assertIn(field_name, schema._meta.fields.keys())

    def test_extend_tags_enabled(self):

        schema = extend_schema_type_tags(self.schema, Site)

        self.assertTrue(hasattr(schema, "resolve_tags"))
        self.assertIsInstance(getattr(schema, "resolve_tags"), types.FunctionType)

    def test_extend_config_context(self):

        schema = extend_schema_type_config_context(DeviceTypeGraphQL, Device)
        self.assertIn("config_context", schema._meta.fields.keys())

    def test_extend_schema_device(self):

        # The below *will* log an error as DeviceTypeGraphQL has already been extended automatically...?
        schema = extend_schema_type(DeviceTypeGraphQL)
        self.assertIn("config_context", schema._meta.fields.keys())
        self.assertTrue(hasattr(schema, "resolve_tags"))
        self.assertIsInstance(getattr(schema, "resolve_tags"), types.FunctionType)

    def test_extend_schema_site(self):

        schema = extend_schema_type(self.schema)
        self.assertNotIn("config_context", schema._meta.fields.keys())
        self.assertTrue(hasattr(schema, "resolve_tags"))
        self.assertIsInstance(getattr(schema, "resolve_tags"), types.FunctionType)

    def test_extend_schema_null_field_choices(self):

        schema = extend_schema_type_null_field_choice(self.schema, Interface)

        self.assertTrue(hasattr(schema, "resolve_mode"))
        self.assertIsInstance(getattr(schema, "resolve_mode"), types.FunctionType)


class GraphQLExtendSchemaRelationship(TestCase):
    def setUp(self):

        site_ct = ContentType.objects.get_for_model(Site)
        rack_ct = ContentType.objects.get_for_model(Rack)
        vlan_ct = ContentType.objects.get_for_model(VLAN)

        self.m2m_1 = Relationship(
            name="Vlan to Rack",
            slug="vlan-rack",
            source_type=rack_ct,
            source_label="My Vlans",
            destination_type=vlan_ct,
            destination_label="My Racks",
            type="many-to-many",
        )
        self.m2m_1.validated_save()

        self.m2m_2 = Relationship(
            name="Another Vlan to Rack",
            slug="vlan-rack-2",
            source_type=rack_ct,
            destination_type=vlan_ct,
            type="many-to-many",
        )
        self.m2m_2.validated_save()

        self.o2m_1 = Relationship(
            name="generic site to vlan",
            slug="site-vlan",
            source_type=site_ct,
            destination_type=vlan_ct,
            type="one-to-many",
        )
        self.o2m_1.validated_save()

        self.o2o_1 = Relationship(
            name="Primary Rack per Site",
            slug="primary-rack-site",
            source_type=rack_ct,
            source_hidden=True,
            destination_type=site_ct,
            destination_label="Primary Rack",
            type="one-to-one",
        )
        self.o2o_1.validated_save()

        self.o2os_1 = Relationship(
            name="Redundant Site",
            slug="redundant-site",
            source_type=site_ct,
            destination_type=site_ct,
            type="symmetric-one-to-one",
        )
        self.o2os_1.validated_save()

        self.o2m_same_type_1 = Relationship(
            name="Some sort of site hierarchy?",
            slug="site-hierarchy",
            source_type=site_ct,
            destination_type=site_ct,
            type="one-to-many",
        )
        self.o2m_same_type_1.validated_save()

        self.site_schema = generate_schema_type(app_name="dcim", model=Site)
        self.vlan_schema = generate_schema_type(app_name="ipam", model=VLAN)

    def test_extend_relationship_default_prefix(self):
        """Verify that relationships are correctly added to the schema."""
        schema = extend_schema_type_relationships(self.vlan_schema, VLAN)

        # Relationships on VLAN
        for rel, peer_side in [
            (self.m2m_1, "source"),
            (self.m2m_2, "source"),
            (self.o2m_1, "source"),
        ]:
            field_name = f"rel_{str_to_var_name(rel.slug)}"
            self.assertIn(field_name, schema._meta.fields.keys())
            self.assertIsInstance(schema._meta.fields[field_name], graphene.types.field.Field)
            if rel.has_many(peer_side):
                self.assertIsInstance(schema._meta.fields[field_name].type, graphene.types.structures.List)
            else:
                self.assertNotIsInstance(schema._meta.fields[field_name].type, graphene.types.structures.List)

        # Relationships not on VLAN
        for rel in [self.o2o_1, self.o2os_1]:
            field_name = f"rel_{str_to_var_name(rel.slug)}"
            self.assertNotIn(field_name, schema._meta.fields.keys())

    @override_settings(GRAPHQL_RELATIONSHIP_PREFIX="pr")
    def test_extend_relationship_w_prefix(self):
        """Verify that relationships are correctly added to the schema when using a custom prefix setting."""
        schema = extend_schema_type_relationships(self.site_schema, Site)

        # Relationships on Site
        for rel, peer_side in [
            (self.o2m_1, "destination"),
            (self.o2o_1, "source"),
            (self.o2os_1, "peer"),
        ]:
            field_name = f"pr_{str_to_var_name(rel.slug)}"
            self.assertIn(field_name, schema._meta.fields.keys())
            self.assertIsInstance(schema._meta.fields[field_name], graphene.types.field.Field)
            if rel.has_many(peer_side):
                self.assertIsInstance(schema._meta.fields[field_name].type, graphene.types.structures.List)
            else:
                self.assertNotIsInstance(schema._meta.fields[field_name].type, graphene.types.structures.List)

        # Special handling of same-type non-symmetric relationships
        for rel in [self.o2m_same_type_1]:
            for peer_side in ["source", "destination"]:
                field_name = f"pr_{str_to_var_name(rel.slug)}_{peer_side}"
                self.assertIn(field_name, schema._meta.fields.keys())
                self.assertIsInstance(schema._meta.fields[field_name], graphene.types.field.Field)
                if rel.has_many(peer_side):
                    self.assertIsInstance(schema._meta.fields[field_name].type, graphene.types.structures.List)
                else:
                    self.assertNotIsInstance(schema._meta.fields[field_name].type, graphene.types.structures.List)

        # Relationships not on Site
        for rel in [self.m2m_1, self.m2m_2]:
            field_name = f"pr_{str_to_var_name(rel.slug)}"
            self.assertNotIn(field_name, schema._meta.fields.keys())


class GraphQLSearchParameters(TestCase):
    def setUp(self):

        self.schema = generate_schema_type(app_name="dcim", model=Site)

    def test_search_parameters(self):

        fields = SiteFilterSet.get_filters().keys()
        params = generate_list_search_parameters(self.schema)
        exclude_filters = ["type"]

        for field in fields:
            if field not in exclude_filters:
                self.assertIn(field, params.keys())
            else:
                self.assertNotIn(field, params.keys())


class GraphQLAPIPermissionTest(TestCase):
    def setUp(self):
        """Initialize the Database with some datas and multiple users associated with different permissions."""
        self.groups = (
            Group.objects.create(name="Group 1"),
            Group.objects.create(name="Group 2"),
        )

        self.users = (
            User.objects.create(username="User 1", is_active=True),
            User.objects.create(username="User 2", is_active=True),
            User.objects.create(username="Super User", is_active=True, is_superuser=True),
            User.objects.create(username="Nobody", is_active=True),
        )

        self.tokens = (
            Token.objects.create(user=self.users[0], key="0123456789abcdef0123456789abcdef01234567"),
            Token.objects.create(user=self.users[1], key="abcd456789abcdef0123456789abcdef01234567"),
            Token.objects.create(user=self.users[2], key="efgh456789abcdef0123456789abcdef01234567"),
            Token.objects.create(user=self.users[3], key="ijkl456789abcdef0123456789abcdef01234567"),
        )

        self.clients = [APIClient(), APIClient(), APIClient(), APIClient()]
        self.clients[0].credentials(HTTP_AUTHORIZATION=f"Token {self.tokens[0].key}")
        self.clients[1].credentials(HTTP_AUTHORIZATION=f"Token {self.tokens[1].key}")
        self.clients[2].credentials(HTTP_AUTHORIZATION=f"Token {self.tokens[2].key}")
        self.clients[3].credentials(HTTP_AUTHORIZATION=f"Token {self.tokens[3].key}")

        self.regions = (
            Region.objects.create(name="Region 1", slug="region1"),
            Region.objects.create(name="Region 2", slug="region2"),
        )

        self.sites = (
            Site.objects.create(name="Site 1", slug="test1", region=self.regions[0]),
            Site.objects.create(name="Site 2", slug="test2", region=self.regions[1]),
        )

        site_object_type = ContentType.objects.get(app_label="dcim", model="site")
        rack_object_type = ContentType.objects.get(app_label="dcim", model="rack")

        # Apply permissions only to User 1 & 2
        for i in range(2):
            # Rack permission
            rack_obj_permission = ObjectPermission.objects.create(
                name=f"Permission Rack {i+1}",
                actions=["view", "add", "change", "delete"],
                constraints={"site__slug": f"test{i+1}"},
            )
            rack_obj_permission.object_types.add(rack_object_type)
            rack_obj_permission.groups.add(self.groups[i])
            rack_obj_permission.users.add(self.users[i])

            site_obj_permission = ObjectPermission.objects.create(
                name=f"Permission Site {i+1}",
                actions=["view", "add", "change", "delete"],
                constraints={"region__slug": f"region{i+1}"},
            )
            site_obj_permission.object_types.add(site_object_type)
            site_obj_permission.groups.add(self.groups[i])
            site_obj_permission.users.add(self.users[i])

        self.rack_grp1 = (
            Rack.objects.create(name="Rack 1-1", site=self.sites[0]),
            Rack.objects.create(name="Rack 1-2", site=self.sites[0]),
        )
        self.rack_grp2 = (
            Rack.objects.create(name="Rack 2-1", site=self.sites[1]),
            Rack.objects.create(name="Rack 2-2", site=self.sites[1]),
        )

        self.api_url = reverse("graphql-api")

        self.get_racks_query = """
        query {
            racks {
                name
            }
        }
        """

        self.get_racks_params_query = """
        query {
            racks(site: "test1") {
                name
            }
        }
        """

        self.get_racks_var_query = """
        query ($site: [String]) {
            racks(site: $site) {
                name
            }
        }
        """

        self.get_sites_racks_query = """
        query {
            sites {
                name
                racks {
                    name
                }
            }
        }
        """

        self.get_rack_query = """
        query ($id: ID!) {
            rack (id: $id) {
                name
            }
        }
        """

    def test_graphql_api_token_with_perm(self):
        """Validate that users can query based on their permissions."""
        # First user
        response = self.clients[0].post(self.api_url, {"query": self.get_racks_query}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data["data"]["racks"], list)
        names = [item["name"] for item in response.data["data"]["racks"]]
        self.assertEqual(names, ["Rack 1-1", "Rack 1-2"])

        # Second user
        response = self.clients[1].post(self.api_url, {"query": self.get_racks_query}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data["data"]["racks"], list)
        names = [item["name"] for item in response.data["data"]["racks"]]
        self.assertEqual(names, ["Rack 2-1", "Rack 2-2"])

    def test_graphql_api_token_super_user(self):
        """Validate a superuser can query everything."""
        response = self.clients[2].post(self.api_url, {"query": self.get_racks_query}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data["data"]["racks"], list)
        names = [item["name"] for item in response.data["data"]["racks"]]
        self.assertEqual(names, ["Rack 1-1", "Rack 1-2", "Rack 2-1", "Rack 2-2"])

    def test_graphql_api_token_no_group(self):
        """Validate users with no permission are not able to query anything by default."""
        response = self.clients[3].post(self.api_url, {"query": self.get_racks_query}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data["data"]["racks"], list)
        names = [item["name"] for item in response.data["data"]["racks"]]
        self.assertEqual(names, [])

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_graphql_api_token_no_group_exempt(self):
        """Validate users with no permission are able to query based on the exempt permissions."""
        response = self.clients[3].post(self.api_url, {"query": self.get_racks_query}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data["data"]["racks"], list)
        names = [item["name"] for item in response.data["data"]["racks"]]
        self.assertEqual(names, ["Rack 1-1", "Rack 1-2", "Rack 2-1", "Rack 2-2"])

    def test_graphql_api_no_token(self):
        """Validate unauthenticated users are not able to query anything by default."""
        client = APIClient()
        response = client.post(self.api_url, {"query": self.get_racks_query}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data["data"]["racks"], list)
        names = [item["name"] for item in response.data["data"]["racks"]]
        self.assertEqual(names, [])

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_graphql_api_no_token_exempt(self):
        """Validate unauthenticated users are able to query based on the exempt permissions."""
        client = APIClient()
        response = client.post(self.api_url, {"query": self.get_racks_query}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data["data"]["racks"], list)
        names = [item["name"] for item in response.data["data"]["racks"]]
        self.assertEqual(names, ["Rack 1-1", "Rack 1-2", "Rack 2-1", "Rack 2-2"])

    def test_graphql_api_wrong_token(self):
        """Validate a wrong token return 403."""
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Token zzzzzzzzzzabcdef0123456789abcdef01234567")
        response = client.post(self.api_url, {"query": self.get_racks_query}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_graphql_query_params(self):
        """Validate query parameters are available for a model."""
        response = self.clients[2].post(self.api_url, {"query": self.get_racks_params_query}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data["data"]["racks"], list)
        names = [item["name"] for item in response.data["data"]["racks"]]
        self.assertEqual(names, ["Rack 1-1", "Rack 1-2"])

    def test_graphql_query_variables(self):
        """Validate graphql variables are working as expected."""
        payload = {"query": self.get_racks_var_query, "variables": {"site": "test1"}}
        response = self.clients[2].post(self.api_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data["data"]["racks"], list)
        names = [item["name"] for item in response.data["data"]["racks"]]
        self.assertEqual(names, ["Rack 1-1", "Rack 1-2"])

        payload = {"query": self.get_racks_var_query, "variables": {"site": "test2"}}
        response = self.clients[2].post(self.api_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data["data"]["racks"], list)
        names = [item["name"] for item in response.data["data"]["racks"]]
        self.assertEqual(names, ["Rack 2-1", "Rack 2-2"])

    def test_graphql_single_object_query(self):
        """Validate graphql query for a single object as opposed to a set of objects also works."""
        payload = {"query": self.get_rack_query, "variables": {"id": Rack.objects.first().pk}}
        response = self.clients[2].post(self.api_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data["data"]["rack"], dict)
        self.assertEqual(response.data["data"]["rack"]["name"], Rack.objects.first().name)

    def test_graphql_query_multi_level(self):
        """Validate request with multiple levels return the proper information, following the permissions."""
        response = self.clients[0].post(self.api_url, {"query": self.get_sites_racks_query}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data["data"]["sites"], list)
        site_names = [item["name"] for item in response.data["data"]["sites"]]
        self.assertEqual(site_names, ["Site 1"])
        rack_names = [item["name"] for item in response.data["data"]["sites"][0]["racks"]]
        self.assertEqual(rack_names, ["Rack 1-1", "Rack 1-2"])

    def test_graphql_query_format(self):
        """Validate application/graphql query is working properly."""
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {self.tokens[2].key}")
        response = client.post(
            self.api_url,
            data=self.get_sites_racks_query,
            content_type="application/graphql",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data["data"]["sites"], list)
        site_names = [item["name"] for item in response.data["data"]["sites"]]
        self.assertEqual(site_names, ["Site 1", "Site 2"])


class GraphQLQueryTest(TestCase):
    """Execute various GraphQL queries and verify their correct responses."""

    @classmethod
    def setUpTestData(cls):
        """Initialize the Database with some datas."""
        super().setUpTestData()
        cls.user = User.objects.create(username="Super User", is_active=True, is_superuser=True)

        # Initialize fake request that will be required to execute GraphQL query
        cls.request = RequestFactory().request(SERVER_NAME="WebRequestContext")
        cls.request.id = uuid.uuid4()
        cls.request.user = cls.user

        # Populate Data
        manufacturer = Manufacturer.objects.create(name="Manufacturer 1", slug="manufacturer-1")
        cls.devicetype = DeviceType.objects.create(
            manufacturer=manufacturer, model="Device Type 1", slug="device-type-1"
        )
        cls.devicerole1 = DeviceRole.objects.create(name="Device Role 1", slug="device-role-1")
        cls.devicerole2 = DeviceRole.objects.create(name="Device Role 2", slug="device-role-2")
        cls.status1 = Status.objects.create(name="status1", slug="status1")
        cls.status2 = Status.objects.create(name="status2", slug="status2")
        cls.region1 = Region.objects.create(name="Region1", slug="region1")
        cls.region2 = Region.objects.create(name="Region2", slug="region2")
        cls.site1 = Site.objects.create(name="Site-1", slug="site-1", asn=65000, status=cls.status1, region=cls.region1)
        cls.site2 = Site.objects.create(name="Site-2", slug="site-2", asn=65099, status=cls.status2, region=cls.region2)
        cls.rack1 = Rack.objects.create(name="Rack 1", site=cls.site1)
        cls.rack2 = Rack.objects.create(name="Rack 2", site=cls.site2)
        cls.tenant1 = Tenant.objects.create(name="Tenant 1", slug="tenant-1")
        cls.tenant2 = Tenant.objects.create(name="Tenant 2", slug="tenant-2")

        cls.vlan1 = VLAN.objects.create(name="VLAN 1", vid=100, site=cls.site1)
        cls.vlan2 = VLAN.objects.create(name="VLAN 2", vid=200, site=cls.site2)

        cls.device1 = Device.objects.create(
            name="Device 1",
            device_type=cls.devicetype,
            device_role=cls.devicerole1,
            site=cls.site1,
            status=cls.status1,
            rack=cls.rack1,
            tenant=cls.tenant1,
            face="front",
            comments="First Device",
        )

        cls.device1_rear_ports = (
            RearPort.objects.create(device=cls.device1, name="Rear Port 1", type=PortTypeChoices.TYPE_8P8C),
            RearPort.objects.create(device=cls.device1, name="Rear Port 2", type=PortTypeChoices.TYPE_8P8C),
            RearPort.objects.create(device=cls.device1, name="Rear Port 3", type=PortTypeChoices.TYPE_8P8C),
            RearPort.objects.create(device=cls.device1, name="Rear Port 4", type=PortTypeChoices.TYPE_8P8C),
        )

        cls.device1_frontports = [
            FrontPort.objects.create(
                device=cls.device1,
                name="Front Port 1",
                type=PortTypeChoices.TYPE_8P8C,
                rear_port=cls.device1_rear_ports[0],
            ),
            FrontPort.objects.create(
                device=cls.device1,
                name="Front Port 2",
                type=PortTypeChoices.TYPE_8P8C,
                rear_port=cls.device1_rear_ports[1],
            ),
            FrontPort.objects.create(
                device=cls.device1,
                name="Front Port 3",
                type=PortTypeChoices.TYPE_8P8C,
                rear_port=cls.device1_rear_ports[2],
            ),
            FrontPort.objects.create(
                device=cls.device1,
                name="Front Port 4",
                type=PortTypeChoices.TYPE_8P8C,
                rear_port=cls.device1_rear_ports[3],
            ),
        ]

        cls.interface11 = Interface.objects.create(
            name="Int1",
            type=InterfaceTypeChoices.TYPE_VIRTUAL,
            device=cls.device1,
            mac_address="00:11:11:11:11:11",
            mode=InterfaceModeChoices.MODE_ACCESS,
            untagged_vlan=cls.vlan1,
        )
        cls.interface12 = Interface.objects.create(
            name="Int2",
            type=InterfaceTypeChoices.TYPE_VIRTUAL,
            device=cls.device1,
        )
        cls.ipaddr1 = IPAddress.objects.create(
            address="10.0.1.1/24", status=cls.status1, assigned_object=cls.interface11
        )

        cls.device2 = Device.objects.create(
            name="Device 2",
            device_type=cls.devicetype,
            device_role=cls.devicerole2,
            site=cls.site1,
            status=cls.status2,
            rack=cls.rack2,
            tenant=cls.tenant2,
            face="rear",
        )

        cls.interface21 = Interface.objects.create(
            name="Int1",
            type=InterfaceTypeChoices.TYPE_VIRTUAL,
            device=cls.device2,
            untagged_vlan=cls.vlan2,
            mode=InterfaceModeChoices.MODE_ACCESS,
        )
        cls.interface22 = Interface.objects.create(
            name="Int2", type=InterfaceTypeChoices.TYPE_1GE_FIXED, device=cls.device2, mac_address="00:12:12:12:12:12"
        )
        cls.ipaddr2 = IPAddress.objects.create(
            address="10.0.2.1/30", status=cls.status2, assigned_object=cls.interface12
        )

        cls.device3 = Device.objects.create(
            name="Device 3",
            device_type=cls.devicetype,
            device_role=cls.devicerole1,
            site=cls.site2,
            status=cls.status1,
        )

        cls.interface31 = Interface.objects.create(
            name="Int1", type=InterfaceTypeChoices.TYPE_VIRTUAL, device=cls.device3
        )
        cls.interface31 = Interface.objects.create(
            name="Mgmt1",
            type=InterfaceTypeChoices.TYPE_VIRTUAL,
            device=cls.device3,
            mgmt_only=True,
            enabled=False,
        )

        cls.cable1 = Cable.objects.create(
            termination_a=cls.interface11,
            termination_b=cls.interface12,
            status=cls.status1,
        )
        cls.cable2 = Cable.objects.create(
            termination_a=cls.interface31,
            termination_b=cls.interface21,
            status=cls.status2,
        )

        context1 = ConfigContext.objects.create(name="context 1", weight=101, data={"a": 123, "b": 456, "c": 777})
        context1.regions.add(cls.region1)

        Provider.objects.create(name="provider 1", slug="provider-1", asn=1)
        Provider.objects.create(name="provider 2", slug="provider-2", asn=4294967295)

        webhook1 = Webhook.objects.create(name="webhook 1", type_delete=True, enabled=False)
        webhook1.content_types.add(ContentType.objects.get_for_model(Device))
        webhook2 = Webhook.objects.create(name="webhook 2", type_update=True, enabled=False)
        webhook2.content_types.add(ContentType.objects.get_for_model(Interface))

        clustertype = ClusterType.objects.create(name="Cluster Type 1", slug="cluster-type-1")
        cluster = Cluster.objects.create(name="Cluster 1", type=clustertype)
        cls.virtualmachine = VirtualMachine.objects.create(
            name="Virtual Machine 1",
            cluster=cluster,
            status=cls.status1,
        )
        cls.vminterface = VMInterface.objects.create(
            virtual_machine=cls.virtualmachine,
            name="eth0",
        )
        cls.vmipaddr = IPAddress.objects.create(
            address="1.1.1.1/32", status=cls.status1, assigned_object=cls.vminterface
        )

        cls.relationship_o2o_1 = Relationship(
            name="Device to VirtualMachine",
            slug="device-to-vm",
            source_type=ContentType.objects.get_for_model(Device),
            destination_type=ContentType.objects.get_for_model(VirtualMachine),
            type="one-to-one",
        )
        cls.relationship_o2o_1.validated_save()

        cls.ro2o_assoc_1 = RelationshipAssociation(
            relationship=cls.relationship_o2o_1,
            source=cls.device1,
            destination=cls.virtualmachine,
        )
        cls.ro2o_assoc_1.validated_save()

        cls.relationship_m2ms_1 = Relationship(
            name="Device Group",
            slug="device-group",
            source_type=ContentType.objects.get_for_model(Device),
            destination_type=ContentType.objects.get_for_model(Device),
            type="symmetric-many-to-many",
        )
        cls.relationship_m2ms_1.validated_save()

        cls.rm2ms_assoc_1 = RelationshipAssociation(
            relationship=cls.relationship_m2ms_1,
            source=cls.device1,
            destination=cls.device2,
        )
        cls.rm2ms_assoc_1.validated_save()
        cls.rm2ms_assoc_2 = RelationshipAssociation(
            relationship=cls.relationship_m2ms_1,
            source=cls.device2,
            destination=cls.device3,
        )
        cls.rm2ms_assoc_2.validated_save()
        cls.rm2ms_assoc_3 = RelationshipAssociation(
            relationship=cls.relationship_m2ms_1,
            source=cls.device3,
            destination=cls.device1,
        )
        cls.rm2ms_assoc_3.validated_save()

        cls.backend = get_default_backend()
        cls.schema = graphene_settings.SCHEMA

    def execute_query(self, query, variables=None):

        document = self.backend.document_from_string(self.schema, query)
        if variables:
            return document.execute(context_value=self.request, variable_values=variables)
        else:
            return document.execute(context_value=self.request)

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_config_context_and_custom_field_data(self):

        query = """
        query {
            devices {
                name
                config_context
                _custom_field_data
            }
            device (id: "%s") {
                name
                config_context
                _custom_field_data
            }
        }
        """ % (
            self.device1.id,
        )

        expected_data = {"a": 123, "b": 456, "c": 777}

        result = self.execute_query(query)

        self.assertIsInstance(result.data["devices"], list)
        self.assertIsInstance(result.data["device"], dict)

        device_names = [item["name"] for item in result.data["devices"]]
        self.assertEqual(sorted(device_names), ["Device 1", "Device 2", "Device 3"])
        self.assertEqual(result.data["device"]["name"], "Device 1")

        config_contexts = [item["config_context"] for item in result.data["devices"]]
        self.assertIsInstance(config_contexts[0], dict)
        self.assertDictEqual(config_contexts[0], expected_data)
        self.assertEqual(result.data["device"]["config_context"], expected_data)

        custom_field_data = [item["_custom_field_data"] for item in result.data["devices"]]
        self.assertIsInstance(custom_field_data[0], dict)
        self.assertEqual(custom_field_data[0], {})
        self.assertEqual(result.data["device"]["_custom_field_data"], {})

    @skip("Works in isolation, fails as part of the overall test suite due to issue #446")
    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_relationship_associations(self):
        """Test queries involving relationship associations."""

        # Query testing for https://github.com/nautobot/nautobot/issues/1228
        query = """
        query {
            device (id: "%s") {
                name
                rel_device_to_vm {
                    id
                }
                rel_device_group {
                    id
                }
            }
        }
        """ % (
            self.device1.id,
        )
        result = self.execute_query(query)

        self.assertIsInstance(result.data, dict, result)
        self.assertIsInstance(result.data["device"], dict, result)
        self.assertEqual(result.data["device"]["name"], self.device1.name)
        self.assertIsInstance(result.data["device"]["rel_device_to_vm"], dict, result)
        self.assertEqual(result.data["device"]["rel_device_to_vm"]["id"], str(self.virtualmachine.id))
        self.assertIsInstance(result.data["device"]["rel_device_group"], list, result)
        self.assertIn(str(self.device2.id), set(item["id"] for item in result.data["device"]["rel_device_group"]))
        self.assertIn(str(self.device3.id), set(item["id"] for item in result.data["device"]["rel_device_group"]))

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_device_role_filter(self):

        query = """
            query {
                devices(role: "device-role-1") {
                    id
                    name
                }
            }
        """
        result = self.execute_query(query)

        self.assertEqual(len(result.data["devices"]), 2)
        device_names = [item["name"] for item in result.data["devices"]]
        self.assertEqual(sorted(device_names), ["Device 1", "Device 3"])

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_with_bad_filter(self):

        query = """
            query {
                devices(role: "EXPECT NO ENTRIES") {
                    id
                    name
                }
            }
        """

        response = self.execute_query(query)
        self.assertEqual(len(response.errors), 1)
        self.assertIsInstance(response.errors[0], GraphQLLocatedError)

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_sites_filter(self):

        filters = (
            ('name: "Site-1"', 1),
            ('name: ["Site-1"]', 1),
            ('name: ["Site-1", "Site-2"]', 2),
            ('name__ic: "Site"', 2),
            ('name__ic: ["Site"]', 2),
            ('name__nic: "Site"', 0),
            ('name__nic: ["Site"]', 0),
            ('region: "region1"', 1),
            ('region: ["region1"]', 1),
            ('region: ["region1", "region2"]', 2),
            ("asn: 65000", 1),
            ("asn: [65099]", 1),
            ("asn: [65000, 65099]", 2),
            (f'id: "{self.site1.pk}"', 1),
            (f'id: ["{self.site1.pk}"]', 1),
            (f'id: ["{self.site1.pk}", "{self.site2.pk}"]', 2),
            ('status: "status1"', 1),
            ('status: ["status2"]', 1),
            ('status: ["status1", "status2"]', 2),
        )

        for filter, nbr_expected_results in filters:
            with self.subTest(msg=f"Checking {filter}", filter=filter, nbr_expected_results=nbr_expected_results):
                query = "query { sites(" + filter + "){ name }}"
                result = self.execute_query(query)
                self.assertIsNone(result.errors)
                self.assertEqual(len(result.data["sites"]), nbr_expected_results)

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_devices_filter(self):

        filters = (
            ('name: "Device 1"', 1),
            ('name: ["Device 1"]', 1),
            ('name: ["Device 1", "Device 2"]', 2),
            ('name__ic: "Device"', 3),
            ('name__ic: ["Device"]', 3),
            ('name__nic: "Device"', 0),
            ('name__nic: ["Device"]', 0),
            (f'id: "{self.device1.pk}"', 1),
            (f'id: ["{self.device1.pk}"]', 1),
            (f'id: ["{self.device1.pk}", "{self.device2.pk}"]', 2),
            ('role: "device-role-1"', 2),
            ('role: ["device-role-1"]', 2),
            ('role: ["device-role-1", "device-role-2"]', 3),
            ('site: "site-1"', 2),
            ('site: ["site-1"]', 2),
            ('site: ["site-1", "site-2"]', 3),
            ('region: "region1"', 2),
            ('region: ["region1"]', 2),
            ('region: ["region1", "region2"]', 3),
            ('face: "front"', 1),
            ('face: "rear"', 1),
            ('status: "status1"', 2),
            ('status: ["status2"]', 1),
            ('status: ["status1", "status2"]', 3),
            ("is_full_depth: true", 3),
            ("is_full_depth: false", 0),
            ("has_primary_ip: true", 0),
            ("has_primary_ip: false", 3),
            ('mac_address: "00:11:11:11:11:11"', 1),
            ('mac_address: ["00:12:12:12:12:12"]', 1),
            ('mac_address: ["00:11:11:11:11:11", "00:12:12:12:12:12"]', 2),
            ('mac_address: "99:11:11:11:11:11"', 0),
            ('q: "first"', 1),
            ('q: "notthere"', 0),
        )

        for filter, nbr_expected_results in filters:
            with self.subTest(msg=f"Checking {filter}", filter=filter, nbr_expected_results=nbr_expected_results):
                query = "query {devices(" + filter + "){ name }}"
                result = self.execute_query(query)
                self.assertIsNone(result.errors)
                self.assertEqual(len(result.data["devices"]), nbr_expected_results)

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_ip_addresses_filter(self):

        filters = (
            ('address: "10.0.1.1"', 1),
            ("family: 4", 3),
            ('status: "status1"', 2),
            ('status: ["status2"]', 1),
            ('status: ["status1", "status2"]', 3),
            ("mask_length: 24", 1),
            ("mask_length: 30", 1),
            ("mask_length: 32", 1),
            ("mask_length: 28", 0),
            ('parent: "10.0.0.0/16"', 2),
            ('parent: "10.0.2.0/24"', 1),
        )

        for filter, nbr_expected_results in filters:
            with self.subTest(msg=f"Checking {filter}", filter=filter, nbr_expected_results=nbr_expected_results):
                query = "query { ip_addresses(" + filter + "){ address }}"
                result = self.execute_query(query)
                self.assertIsNone(result.errors)
                self.assertEqual(len(result.data["ip_addresses"]), nbr_expected_results)

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_ip_addresses_assigned_object(self):
        """Query IP Address assigned_object values."""

        query = """\
query {
    ip_addresses {
        address
        assigned_object {
            ... on InterfaceType {
                name
                device { name }
            }
            ... on VMInterfaceType {
                name
                virtual_machine { name }
            }
        }
        family
        interface { name }
        vminterface { name }
    }
}"""
        result = self.execute_query(query)
        self.assertIsNone(result.errors)
        self.assertEqual(len(result.data["ip_addresses"]), 3)
        for entry in result.data["ip_addresses"]:
            self.assertIn(
                entry["address"], (str(self.ipaddr1.address), str(self.ipaddr2.address), str(self.vmipaddr.address))
            )
            self.assertIn("assigned_object", entry)
            self.assertIn(entry["family"], (4, 6))
            if entry["address"] == str(self.vmipaddr.address):
                self.assertEqual(entry["assigned_object"]["name"], self.vminterface.name)
                self.assertEqual(entry["vminterface"]["name"], self.vminterface.name)
                self.assertIsNone(entry["interface"])
                self.assertIn("virtual_machine", entry["assigned_object"])
                self.assertNotIn("device", entry["assigned_object"])
                self.assertEqual(entry["assigned_object"]["virtual_machine"]["name"], self.virtualmachine.name)
            else:
                self.assertIn(entry["assigned_object"]["name"], (self.interface11.name, self.interface12.name))
                self.assertIn(entry["interface"]["name"], (self.interface11.name, self.interface12.name))
                self.assertIsNone(entry["vminterface"])
                self.assertIn("device", entry["assigned_object"])
                self.assertNotIn("virtual_machine", entry["assigned_object"])
                self.assertEqual(entry["assigned_object"]["device"]["name"], self.device1.name)

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_cables_filter(self):

        filters = (
            (f'device_id: "{self.device1.id}"', 1),
            ('device: "Device 3"', 1),
            ('device: ["Device 1", "Device 3"]', 2),
            (f'rack_id: "{self.rack1.id}"', 1),
            ('rack: "Rack 2"', 1),
            ('rack: ["Rack 1", "Rack 2"]', 2),
            (f'site_id: "{self.site1.id}"', 2),
            ('site: "site-2"', 1),
            ('site: ["site-1", "site-2"]', 2),
            (f'tenant_id: "{self.tenant1.id}"', 1),
            ('tenant: "tenant-2"', 1),
            ('tenant: ["tenant-1", "tenant-2"]', 2),
        )

        for filter, nbr_expected_results in filters:
            with self.subTest(msg=f"Checking {filter}", filter=filter, nbr_expected_results=nbr_expected_results):
                query = "query { cables(" + filter + "){ id }}"
                result = self.execute_query(query)
                self.assertIsNone(result.errors)
                self.assertEqual(len(result.data["cables"]), nbr_expected_results)

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_frontport_filter_second_level(self):
        """Test "second-level" filtering of FrontPorts within a Devices query."""

        filters = (
            (f'name: "{self.device1_frontports[0].name}"', 1),
            (f'device: "{self.device1.name}"', 4),
            (f'_type: "{PortTypeChoices.TYPE_8P8C}"', 4),
        )

        for filter, nbr_expected_results in filters:
            with self.subTest(msg=f"Checking {filter}", filter=filter, nbr_expected_results=nbr_expected_results):
                query = "query { devices{ frontports(" + filter + "){ id }}}"
                result = self.execute_query(query)
                self.assertIsNone(result.errors)
                self.assertEqual(len(result.data["devices"][0]["frontports"]), nbr_expected_results)

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_frontport_filter_third_level(self):
        """Test "third-level" filtering of FrontPorts within Devices within Sites."""

        filters = (
            (f'name: "{self.device1_frontports[0].name}"', 1),
            (f'device: "{self.device1.name}"', 4),
            (f'_type: "{PortTypeChoices.TYPE_8P8C}"', 4),
        )

        for filter, nbr_expected_results in filters:
            with self.subTest(msg=f"Checking {filter}", filter=filter, nbr_expected_results=nbr_expected_results):
                query = "query { sites{ devices{ frontports(" + filter + "){ id }}}}"
                result = self.execute_query(query)
                self.assertIsNone(result.errors)
                self.assertEqual(len(result.data["sites"][0]["devices"][0]["frontports"]), nbr_expected_results)

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_interfaces_filter(self):
        """Test custom interface filter fields and boolean, not other concrete fields."""

        filters = (
            (f'device_id: "{self.device1.id}"', 2),
            ('device: "Device 3"', 2),
            ('device: ["Device 1", "Device 3"]', 4),
            ('kind: "virtual"', 5),
            ('mac_address: "00:11:11:11:11:11"', 1),
            ("vlan: 100", 1),
            (f'vlan_id: "{self.vlan1.id}"', 1),
            ("mgmt_only: true", 1),
            ("enabled: false", 1),
        )

        for filter, nbr_expected_results in filters:
            with self.subTest(msg=f"Checking {filter}", filter=filter, nbr_expected_results=nbr_expected_results):
                query = "query { interfaces(" + filter + "){ id }}"
                result = self.execute_query(query)
                self.assertIsNone(result.errors)
                self.assertEqual(len(result.data["interfaces"]), nbr_expected_results)

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_interfaces_filter_second_level(self):
        """Test "second-level" filtering of Interfaces within a Devices query."""

        filters = (
            (f'device_id: "{self.device1.id}"', 2),
            ('kind: "virtual"', 2),
            ('mac_address: "00:11:11:11:11:11"', 1),
            ("vlan: 100", 1),
            (f'vlan_id: "{self.vlan1.id}"', 1),
        )

        for filter, nbr_expected_results in filters:
            with self.subTest(msg=f"Checking {filter}", filter=filter, nbr_expected_results=nbr_expected_results):
                query = "query { devices{ interfaces(" + filter + "){ id }}}"
                result = self.execute_query(query)
                self.assertIsNone(result.errors)
                self.assertEqual(len(result.data["devices"][0]["interfaces"]), nbr_expected_results)

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_interfaces_filter_third_level(self):
        """Test "third-level" filtering of Interfaces within Devices within Sites."""

        filters = (
            (f'device_id: "{self.device1.id}"', 2),
            ('kind: "virtual"', 2),
            ('mac_address: "00:11:11:11:11:11"', 1),
            ("vlan: 100", 1),
            (f'vlan_id: "{self.vlan1.id}"', 1),
        )

        for filter, nbr_expected_results in filters:
            with self.subTest(msg=f"Checking {filter}", filter=filter, nbr_expected_results=nbr_expected_results):
                query = "query { sites{ devices{ interfaces(" + filter + "){ id }}}}"
                result = self.execute_query(query)
                self.assertIsNone(result.errors)
                self.assertEqual(len(result.data["sites"][0]["devices"][0]["interfaces"]), nbr_expected_results)

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_interfaces_connected_endpoint(self):
        """Test querying interfaces for their connected endpoints."""

        query = """\
query {
    interfaces {
        connected_endpoint {
            ... on InterfaceType {
                name
                device { name }
            }
        }
        connected_interface {
            name
            device { name }
        }
        connected_console_server_port { id }
        connected_circuit_termination { id }
    }
}"""

        result = self.execute_query(query)
        self.assertIsNone(result.errors)
        for interface_entry in result.data["interfaces"]:
            if interface_entry["connected_endpoint"] is None:
                self.assertIsNone(interface_entry["connected_interface"])
            else:
                self.assertEqual(
                    interface_entry["connected_endpoint"]["name"], interface_entry["connected_interface"]["name"]
                )
                self.assertEqual(
                    interface_entry["connected_endpoint"]["device"]["name"],
                    interface_entry["connected_interface"]["device"]["name"],
                )
            # TODO: it would be nice to have connections to console server ports and circuit terminations to test!
            self.assertIsNone(interface_entry["connected_console_server_port"])
            self.assertIsNone(interface_entry["connected_circuit_termination"])

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_interfaces_cable_peer(self):
        """Test querying interfaces for their cable peers"""

        query = """\
query {
    interfaces {
        id
        cable_peer_circuit_termination { id }
        cable_peer_interface { id }
        cable_peer_front_port { id }
        cable_peer_rear_port { id }
    }
}"""

        result = self.execute_query(query)
        self.assertIsNone(result.errors)
        for interface_entry in result.data["interfaces"]:
            intf_obj = Interface.objects.get(id=interface_entry["id"])
            cable_peer = intf_obj.get_cable_peer()

            # Extract Expected Properties from Interface object
            cable_peer_circuit_termination = (
                {"id": str(cable_peer.id)} if isinstance(cable_peer, CircuitTermination) else None
            )
            cable_peer_interface = {"id": str(cable_peer.id)} if isinstance(cable_peer, Interface) else None
            cable_peer_front_port = {"id": str(cable_peer.id)} if isinstance(cable_peer, FrontPort) else None
            cable_peer_rear_port = {"id": str(cable_peer.id)} if isinstance(cable_peer, RearPort) else None

            # Assert GraphQL returned properties match those expected
            self.assertEqual(interface_entry["cable_peer_circuit_termination"], cable_peer_circuit_termination)
            self.assertEqual(interface_entry["cable_peer_interface"], cable_peer_interface)
            self.assertEqual(interface_entry["cable_peer_front_port"], cable_peer_front_port)
            self.assertEqual(interface_entry["cable_peer_rear_port"], cable_peer_rear_port)

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_interfaces_mode(self):
        """Test querying interfaces for their mode and make sure a string or None is returned."""

        query = """\
query {
    devices(name: "Device 1") {
        interfaces {
            name
            mode
        }
    }
}"""

        result = self.execute_query(query)
        self.assertIsNone(result.errors)
        for intf in result.data["devices"][0]["interfaces"]:
            intf_name = intf["name"]
            if intf_name == "Int1":
                self.assertEqual(intf["mode"], InterfaceModeChoices.MODE_ACCESS.upper())
            elif intf_name == "Int2":
                self.assertIsNone(intf["mode"])

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_providers_filter(self):
        """Test provider filtering by ASN (issue #428)."""
        filters = (
            ("asn: [4294967295]", 1),
            ("asn: [1, 4294967295]", 2),
        )

        for filterv, nbr_expected_results in filters:
            with self.subTest(msg=f"Checking {filterv}", filterv=filterv, nbr_expected_results=nbr_expected_results):
                query = "query { providers (" + filterv + "){ id asn }}"
                result = self.execute_query(query)
                self.assertIsNone(result.errors)
                self.assertEqual(len(result.data["providers"]), nbr_expected_results)
                for provider in result.data["providers"]:
                    self.assertEqual(provider["asn"], Provider.objects.get(id=provider["id"]).asn)

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_webhooks_filter(self):
        """Test webhook querying and filtering with content types."""
        filters = (
            ('content_types: ["dcim.device"]', 1),
            ('content_types: ["dcim.interface"]', 1),
            # Since content_types is a many-to-many field, this query is an AND, not an OR
            ('content_types: ["dcim.device", "dcim.interface"]', 0),
            ('content_types: ["ipam.ipaddress"]', 0),
        )

        for filterv, nbr_expected_results in filters:
            with self.subTest(msg=f"Checking {filterv}", filterv=filterv, nbr_expected_results=nbr_expected_results):
                query = "query { webhooks (" + filterv + "){ id name content_types {app_label model}}}"
                result = self.execute_query(query)
                self.assertIsNone(result.errors)
                self.assertEqual(len(result.data["webhooks"]), nbr_expected_results)

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_query_device_types(self):
        """Test querying of device types, specifically checking for issue #1203."""
        query = """
        query {
            device_types {
                model
            }
        }
        """
        result = self.execute_query(query)
        self.assertIsNone(result.errors)
        self.assertIsInstance(result.data, dict, result)
        self.assertIsInstance(result.data["device_types"], list, result)
        self.assertEqual(result.data["device_types"][0]["model"], self.devicetype.model, result)
