<template>
	<div class="app-container">
		<h1>{{ title }}</h1>
		<EmployeeList :employees="employees" />
	</div>
</template>

<script setup>
import { ref, computed } from "vue"
import EmployeeList from "@/components/EmployeeList.vue"

const title = ref("HR Management")

const employees = computed(() => {
	return getActiveEmployees()
})

function getActiveEmployees() {
	return []
}
</script>
